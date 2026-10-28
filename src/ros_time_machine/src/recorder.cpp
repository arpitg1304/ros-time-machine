// Copyright 2024 ROS Time Machine Contributors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "ros_time_machine/recorder.hpp"
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <sstream>
#include <thread>
#include <chrono>
#include <tuple>
#include <rosbag2_cpp/writers/sequential_writer.hpp>
#include <rosbag2_storage/storage_options.hpp>

namespace ros_time_machine
{

namespace
{
int64_t toNs(double seconds)
{
  return static_cast<int64_t>(seconds * 1e9);
}

// How often the dispatcher checks for closed windows and expired pins
constexpr std::chrono::milliseconds kDispatchPeriod(50);

// Humble's SerializedBagMessage has time_stamp; Jazzy has send_timestamp + recv_timestamp
template<class M>
auto setBagTimestamp(M & msg, int64_t t, int)->decltype(msg.send_timestamp = t, void())
{
  msg.send_timestamp = t;
  msg.recv_timestamp = t;
}

template<class M>
void setBagTimestamp(M & msg, int64_t t, long)
{
  msg.time_stamp = t;
}
}  // namespace

Recorder::Recorder(
  rclcpp::Node::SharedPtr node,
  rclcpp::Logger logger)
  : node_(node),
    logger_(logger),
    buffer_manager_(nullptr),
    time_source_("ros"),
    shutdown_(false),
    active_recordings_(0),
    completed_recordings_(0),
    failed_recordings_(0),
    initialized_(false)
{
}

Recorder::~Recorder()
{
  dispatch_timer_.reset();

  // Write whatever data pending clips have rather than losing them
  if (initialized_) {
    // Far future, but with headroom for the queue's own arithmetic
    auto pending = trigger_queue_.popReady(std::numeric_limits<int64_t>::max() / 2);
    if (!pending.empty()) {
      RCLCPP_INFO(logger_, "Writing %zu pending clip(s) before shutdown", pending.size());
    }
    for (const auto & clip : pending) {
      enqueueJob(clip);
    }
  }

  // Signal shutdown (under the lock, so no worker misses the wakeup)
  {
    std::lock_guard<std::mutex> lock(queue_mutex_);
    shutdown_ = true;
  }
  queue_cv_.notify_all();

  // Wait for all worker threads to finish
  for (auto & thread : worker_threads_) {
    if (thread.joinable()) {
      thread.join();
    }
  }

  // The guard captures this recorder; the buffer manager may outlive it
  if (buffer_manager_) {
    buffer_manager_->setEvictionGuard(nullptr, 0.0);
  }

  RCLCPP_INFO(logger_, "Recorder shutdown complete");
}

bool Recorder::initialize(
  const TimeMachineConfig & config,
  BufferManager & buffer_manager)
{
  RCLCPP_INFO(logger_, "Initializing Recorder...");

  config_ = config;
  buffer_manager_ = &buffer_manager;

  bool use_sim_time = false;
  if (node_->get_parameter("use_sim_time", use_sim_time) && use_sim_time) {
    time_source_ = "sim";
  }

  TriggerBudget budget;
  budget.max_clips_per_hour = config.global.max_clips_per_hour;
  budget.merge_gap_seconds = config.global.merge_gap;
  budget.max_clip_duration = config.global.max_clip_duration;
  trigger_queue_.setBudget(budget);
  for (const auto & snapshot : config.snapshots) {
    trigger_queue_.setProfileRateLimit(snapshot.name, snapshot.rate_limit.max_triggers_per_minute);
  }

  // Keep pinned ranges and pending clips alive in the buffers until written
  buffer_manager.setEvictionGuard(
    [this](int64_t t_ns) {
      return pin_manager_.covers(t_ns) || trigger_queue_.covers(t_ns);
    },
    config.global.pin_budget_fraction);

  // Create worker threads
  size_t num_threads = config.global.recording_threads;
  RCLCPP_INFO(logger_, "Starting %zu recording worker threads", num_threads);

  for (size_t i = 0; i < num_threads; ++i) {
    worker_threads_.emplace_back(&Recorder::workerThread, this, i);
  }

  // Wall timer: readiness is judged against the node's ROS clock inside the callback,
  // so this keeps working when sim time is paused or replayed faster than real time
  dispatch_timer_ = node_->create_wall_timer(
    kDispatchPeriod, std::bind(&Recorder::dispatchTimerCallback, this));

  initialized_ = true;
  RCLCPP_INFO(logger_, "Recorder initialized successfully (time source: %s)",
              time_source_.c_str());

  return true;
}

void Recorder::handleTrigger(
  const TriggerEvent & event,
  const SnapshotConfig & snapshot_config)
{
  EventRequest request;
  request.profile = snapshot_config.name;
  request.event_ns = event.trigger_time.nanoseconds();
  request.label = snapshot_config.name;
  request.description = snapshot_config.description;
  request.confidence = 1.0f;
  request.source = "rule:" + event.trigger_topic;
  std::ostringstream version;
  version << snapshot_config.trigger.condition.field << "=" << event.trigger_value;
  request.source_version = version.str();
  submitEvent(request);
}

const SnapshotConfig * Recorder::findProfile(const std::string & name) const
{
  if (config_.snapshots.empty()) {
    return nullptr;
  }
  if (name.empty()) {
    return &config_.snapshots.front();
  }
  for (const auto & snapshot : config_.snapshots) {
    if (snapshot.name == name) {
      return &snapshot;
    }
  }
  return nullptr;
}

SubmitResult Recorder::submitEvent(EventRequest request)
{
  SubmitResult result;
  if (!initialized_) {
    result.message = "recorder not initialized";
    RCLCPP_ERROR(logger_, "Recorder not initialized");
    return result;
  }

  const SnapshotConfig * profile = findProfile(request.profile);
  if (!profile) {
    result.outcome = SubmitOutcome::REJECTED_INVALID;
    result.message = "unknown profile '" + request.profile + "'";
    RCLCPP_WARN(logger_, "Event '%s' rejected: %s", request.label.c_str(), result.message.c_str());
    return result;
  }

  int64_t now_ns = node_->now().nanoseconds();
  request.received_ns = now_ns;
  request.profile = profile->name;
  if (request.event_ns == 0) {
    request.event_ns = now_ns;
    // An explicit window without an event time: keep the event inside the window
    if (request.start_ns != 0 && request.end_ns != 0 && request.start_ns <= request.end_ns) {
      request.event_ns = std::clamp(now_ns, request.start_ns, request.end_ns);
    }
  }
  if (request.start_ns == 0) {
    request.start_ns = request.event_ns - toNs(profile->recording.pre_event_duration);
  }
  if (request.end_ns == 0) {
    request.end_ns = request.event_ns + toNs(profile->recording.post_event_duration);
  }
  // Early in sim time a window can reach before t=0, which rclcpp::Time cannot hold
  request.start_ns = std::max<int64_t>(request.start_ns, 0);
  if (request.label.empty()) {
    request.label = profile->name;
  }

  result = trigger_queue_.submit(request, now_ns);

  double lag = (now_ns - request.event_ns) / 1e9;
  if (result.accepted()) {
    RCLCPP_INFO(logger_,
                "Event '%s' from %s -> '%s' clip %lu %s [%.2fs before event, %.2fs after; "
                "arrived %.2fs after event]",
                request.label.c_str(), request.source.c_str(), request.profile.c_str(),
                static_cast<unsigned long>(result.clip_id),
                result.outcome == SubmitOutcome::MERGED ? "merged" : "queued",
                (request.event_ns - request.start_ns) / 1e9,
                (request.end_ns - request.event_ns) / 1e9, lag);
  } else {
    RCLCPP_WARN(logger_, "Event '%s' from %s rejected: %s",
                request.label.c_str(), request.source.c_str(), result.message.c_str());
  }
  return result;
}

PinResult Recorder::pinRange(
  int64_t start_ns, int64_t end_ns, const std::string & profile_name,
  double ttl_seconds, const std::string & reason)
{
  PinResult result;
  const SnapshotConfig * profile = findProfile(profile_name);
  if (!profile) {
    result.message = "unknown profile '" + profile_name + "'";
    return result;
  }
  if (start_ns == 0 && end_ns == 0) {
    result.message = "pin needs a start or end time";
    return result;
  }
  if (start_ns > end_ns && end_ns != 0) {
    std::swap(start_ns, end_ns);
  }

  int64_t now_ns = node_->now().nanoseconds();
  if (end_ns == 0) {
    end_ns = now_ns;
  }
  if (start_ns == 0) {
    // Store a real time: 0 would mean "profile default" once the pin becomes a clip
    start_ns = std::max<int64_t>(end_ns - toNs(profile->recording.pre_event_duration), 1);
  }
  if (start_ns > end_ns) {
    result.message = "pin start is after its end";
    return result;
  }
  double ttl = ttl_seconds > 0.0 ? ttl_seconds : profile->pins.default_ttl;

  // Earliest time from which every profile topic still has data
  int64_t clamped = start_ns;
  for (const auto & topic : profile->recording.topics) {
    auto oldest = buffer_manager_->getOldestTimestamp(topic).nanoseconds();
    if (oldest > 0) {
      clamped = std::max(clamped, oldest);
    }
  }

  result.pin_id = pin_manager_.add(start_ns, end_ns, ttl, now_ns, profile->name, reason);
  result.success = true;
  result.clamped_start_ns = std::min(clamped, end_ns);
  result.message = "pinned";
  RCLCPP_INFO(logger_, "Pin %lu on '%s': %.2fs window, ttl %.1fs (%s)",
              static_cast<unsigned long>(result.pin_id), profile->name.c_str(),
              (end_ns - start_ns) / 1e9, ttl, reason.c_str());
  return result;
}

bool Recorder::releasePin(
  uint64_t pin_id, bool keep, const EventRequest & metadata, std::string & message)
{
  auto pin = pin_manager_.get(pin_id);
  if (!pin) {
    message = "unknown or already resolved pin " + std::to_string(pin_id);
    return false;
  }

  bool success = true;
  message = "released";

  if (keep) {
    const SnapshotConfig * profile = findProfile(pin->profile);
    EventRequest request = metadata;
    request.profile = pin->profile;
    request.start_ns = pin->start_ns;
    request.end_ns = pin->end_ns;
    if (request.event_ns == 0) {
      // Place the event where a profile window would put it, inside the pin
      int64_t post = profile ? toNs(profile->recording.post_event_duration) : 0;
      request.event_ns = std::clamp(pin->end_ns - post, pin->start_ns, pin->end_ns);
    }
    if (request.label.empty()) {
      request.label = pin->reason.empty() ? pin->profile : pin->reason;
    }
    if (request.source.empty()) {
      request.source = "pin";
    }
    // Submit before removing the pin so the data is never unprotected
    auto result = submitEvent(request);
    success = result.accepted();
    message = result.message;
  }

  pin_manager_.take(pin_id);
  buffer_manager_->pruneRetained();
  RCLCPP_INFO(logger_, "Pin %lu %s", static_cast<unsigned long>(pin_id),
              keep ? (success ? "kept" : "kept but clip rejected") : "released");
  return success;
}

void Recorder::dispatchTimerCallback()
{
  int64_t now_ns = node_->now().nanoseconds();

  // Pins nobody decided on resolve by their profile's timeout policy
  bool released_pins = false;
  for (const auto & pin : pin_manager_.expired(now_ns)) {
    const SnapshotConfig * profile = findProfile(pin.profile);
    if (profile && profile->pins.keep_on_timeout) {
      EventRequest request;
      request.profile = pin.profile;
      request.start_ns = pin.start_ns;
      request.end_ns = pin.end_ns;
      request.event_ns = std::clamp(
        pin.end_ns - toNs(profile->recording.post_event_duration), pin.start_ns, pin.end_ns);
      request.label = pin.reason.empty() ? pin.profile : pin.reason;
      request.description = "Pin timed out without a decision (keep_on_timeout)";
      request.confidence = 0.0f;
      request.source = "pin:timeout";
      submitEvent(request);
      RCLCPP_WARN(logger_, "Pin %lu timed out: kept by policy",
                  static_cast<unsigned long>(pin.id));
    } else {
      RCLCPP_INFO(logger_, "Pin %lu timed out: dropped by policy",
                  static_cast<unsigned long>(pin.id));
    }
    // Only now, with any kept clip queued, may the pin's data become evictable
    pin_manager_.take(pin.id);
    released_pins = true;
  }
  if (released_pins) {
    buffer_manager_->pruneRetained();
  }

  auto settle_ns = toNs(config_.global.write_settle_time);
  for (const auto & clip : trigger_queue_.popReady(now_ns, settle_ns)) {
    enqueueJob(clip);
  }
}

void Recorder::enqueueJob(const PendingClip & clip)
{
  const SnapshotConfig * profile = findProfile(clip.profile);
  if (!profile) {
    trigger_queue_.complete(clip.id);
    return;
  }

  RecordingJob job;
  job.snapshot_name = clip.profile;
  job.clip = clip;
  job.snapshot_config = *profile;
  job.status = RecordingStatus::PENDING;
  job.message_count = 0;

  {
    std::lock_guard<std::mutex> lock(queue_mutex_);
    job_queue_.push(job);
  }
  queue_cv_.notify_one();
}

void Recorder::workerThread(size_t thread_id)
{
  RCLCPP_DEBUG(logger_, "Worker thread %zu started", thread_id);

  while (true) {
    RecordingJob job;

    // Wait for a job
    {
      std::unique_lock<std::mutex> lock(queue_mutex_);
      queue_cv_.wait(lock, [this] {
        return !job_queue_.empty() || shutdown_;
      });

      if (job_queue_.empty()) {
        break;  // Shutdown with nothing left to write
      }

      job = job_queue_.front();
      job_queue_.pop();
    }

    // Process the job
    active_recordings_++;
    try {
      processRecording(job);
    } catch (const std::exception & e) {
      job.status = RecordingStatus::FAILED;
      job.error_message = e.what();
      RCLCPP_ERROR(logger_, "Recording '%s' failed: %s", job.snapshot_name.c_str(), e.what());
    }
    active_recordings_--;

    if (job.status == RecordingStatus::COMPLETED) {
      completed_recordings_++;
    } else if (job.status == RecordingStatus::FAILED) {
      failed_recordings_++;
    }

    // The clip's data is no longer needed: let the buffers evict it
    trigger_queue_.complete(job.clip.id);
    buffer_manager_->pruneRetained();

    // Called under the lock so setClipCallback(nullptr) waits for an in-progress call
    std::lock_guard<std::mutex> lock(callback_mutex_);
    if (clip_callback_) {
      ClipResult result;
      result.snapshot_name = job.snapshot_name;
      result.output_path = job.output_path;
      result.status = job.status;
      result.clip = job.clip;
      result.message_count = job.message_count;
      result.topic_count = job.snapshot_config.recording.topics.size();
      result.error_message = job.error_message;
      clip_callback_(result);
    }
  }

  RCLCPP_DEBUG(logger_, "Worker thread %zu stopped", thread_id);
}

void Recorder::processRecording(RecordingJob & job)
{
  const PendingClip & clip = job.clip;
  const EventRequest & primary = clip.primary();
  rclcpp::Time start_time(clip.start_ns, RCL_ROS_TIME);
  rclcpp::Time end_time(clip.end_ns, RCL_ROS_TIME);

  RCLCPP_INFO(logger_,
              "Processing clip %lu for snapshot '%s' (%zu event(s), %.2fs window)",
              static_cast<unsigned long>(clip.id), job.snapshot_name.c_str(),
              clip.events.size(), (clip.end_ns - clip.start_ns) / 1e9);

  job.status = RecordingStatus::POST_EVENT;

  // Collect messages from buffer manager for all topics
  std::map<std::string, std::vector<SerializedMessage>> all_messages;
  std::vector<TopicCoverage> coverage;

  for (const auto & topic : job.snapshot_config.recording.topics) {
    // Did the buffer still reach back to the window start? (before reading, so a
    // concurrent eviction can only make this conservative)
    int64_t oldest_ns = buffer_manager_->getOldestTimestamp(topic).nanoseconds();

    auto messages = buffer_manager_->getMessages(topic, start_time, end_time);

    TopicCoverage cov;
    cov.name = topic;
    cov.type = buffer_manager_->getTopicType(topic);
    cov.message_count = messages.size();
    cov.pre_event_complete = oldest_ns > 0 && oldest_ns <= clip.start_ns;
    if (!messages.empty()) {
      cov.first_ns = messages.front().timestamp.nanoseconds();
      cov.last_ns = messages.back().timestamp.nanoseconds();
      job.message_count += messages.size();
      all_messages[topic] = std::move(messages);
    } else {
      RCLCPP_WARN(logger_, "No messages found for topic %s in time range",
                  topic.c_str());
    }
    coverage.push_back(cov);
  }

  if (all_messages.empty()) {
    RCLCPP_ERROR(logger_, "No messages retrieved for any topic, aborting recording");
    job.status = RecordingStatus::FAILED;
    job.error_message = "no messages in window";
    return;
  }

  RCLCPP_INFO(logger_, "Retrieved %zu total messages from %zu topics",
              job.message_count, all_messages.size());

  job.output_path = generateOutputPath(
    job.snapshot_name,
    rclcpp::Time(primary.event_ns, RCL_ROS_TIME),
    job.snapshot_config.output,
    primary.label);

  // Write to rosbag
  job.status = RecordingStatus::WRITING;
  bool success = writeRosbag(job, all_messages);
  {
    // The directory exists now (or the write failed): the name needs no reservation
    std::lock_guard<std::mutex> lock(paths_mutex_);
    reserved_paths_.erase(job.output_path);
  }

  if (!success) {
    job.status = RecordingStatus::FAILED;
    job.error_message = "rosbag write failed";
    RCLCPP_ERROR(logger_, "Recording failed: %s", job.output_path.c_str());
    return;
  }

  // The bag directory exists now; explain the clip next to it
  SidecarInfo sidecar;
  sidecar.clip = std::filesystem::path(job.output_path).filename().string();
  sidecar.storage = job.snapshot_config.output.format;
  sidecar.time_source = time_source_;
  sidecar.window = clip;
  sidecar.topics = coverage;
  sidecar.robot_id = config_.global.robot_id;
  sidecar.robot_metadata = config_.global.metadata;
  sidecar.written_at_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::system_clock::now().time_since_epoch()).count();

  std::string sidecar_error;
  auto sidecar_path = (std::filesystem::path(job.output_path) / kSidecarFileName).string();
  if (!writeSidecar(sidecar_path, sidecar, &sidecar_error)) {
    RCLCPP_WARN(logger_, "Clip written but sidecar failed: %s", sidecar_error.c_str());
  }

  job.status = RecordingStatus::COMPLETED;
  RCLCPP_INFO(logger_,
              "Recording completed successfully: %s (%zu messages, '%s' from %s)",
              job.output_path.c_str(),
              job.message_count,
              primary.label.c_str(),
              primary.source.c_str());
}

std::string Recorder::generateOutputPath(
  const std::string & snapshot_name,
  const rclcpp::Time & trigger_time,
  const OutputConfig & output_config,
  const std::string & label)
{
  // Create base directory if it doesn't exist
  std::filesystem::path base_path(output_config.path);
  std::filesystem::create_directories(base_path);

  // Generate timestamp string
  auto time_ns = trigger_time.nanoseconds();
  auto time_s = time_ns / 1000000000;
  auto time_ns_remainder = time_ns % 1000000000;

  std::ostringstream timestamp_stream;
  timestamp_stream << time_s << "_" << std::setfill('0') << std::setw(9) << time_ns_remainder;
  std::string timestamp = timestamp_stream.str();

  // Labels come from detectors: keep them filesystem-safe
  std::string safe_label = label.empty() ? snapshot_name : label;
  for (auto & c : safe_label) {
    if (!std::isalnum(static_cast<unsigned char>(c)) && c != '-' && c != '_') {
      c = '_';
    }
  }

  // Replace placeholders in pattern
  std::string filename = output_config.name_pattern;
  const std::vector<std::pair<std::string, std::string>> placeholders = {
    {"{snapshot_name}", snapshot_name},
    {"{timestamp}", timestamp},
    {"{label}", safe_label},
  };
  for (const auto & [placeholder, value] : placeholders) {
    size_t pos = 0;
    while ((pos = filename.find(placeholder, pos)) != std::string::npos) {
      filename.replace(pos, placeholder.length(), value);
      pos += value.length();
    }
  }

  // rosbag2 refuses to write into an existing directory, and another worker may be
  // about to create one with the same name
  std::lock_guard<std::mutex> lock(paths_mutex_);
  std::filesystem::path path = base_path / filename;
  for (int suffix = 1;
    std::filesystem::exists(path) || reserved_paths_.count(path.string()); ++suffix)
  {
    path = base_path / (filename + "_" + std::to_string(suffix));
  }
  reserved_paths_.insert(path.string());
  return path.string();
}

bool Recorder::writeRosbag(
  RecordingJob & job,
  const std::map<std::string, std::vector<SerializedMessage>> & messages)
{
  try {
    // Create rosbag2 writer
    rosbag2_cpp::Writer writer;

    rosbag2_storage::StorageOptions storage_options;
    storage_options.uri = job.output_path;
    storage_options.storage_id = job.snapshot_config.output.format;  // "mcap" or "sqlite3"

    rosbag2_cpp::ConverterOptions converter_options;
    converter_options.input_serialization_format = "cdr";
    converter_options.output_serialization_format = "cdr";

    writer.open(storage_options, converter_options);

    // Create topic metadata for all topics
    for (const auto & [topic_name, topic_messages] : messages) {
      if (topic_messages.empty()) {
        continue;
      }

      const auto & first_msg = topic_messages[0];

      rosbag2_storage::TopicMetadata topic_metadata;
      topic_metadata.name = topic_name;
      topic_metadata.type = first_msg.type_name;
      topic_metadata.serialization_format = "cdr";

      writer.create_topic(topic_metadata);
    }

    // Write all messages in chronological order
    // Collect all messages with their timestamps
    std::vector<std::pair<int64_t, const SerializedMessage *>> sorted_messages;

    for (const auto & [topic_name, topic_messages] : messages) {
      for (const auto & msg : topic_messages) {
        sorted_messages.emplace_back(msg.timestamp.nanoseconds(), &msg);
      }
    }

    // Sort by timestamp, then topic: two clips of the same window are identical.
    // stable_sort keeps arrival order within a topic.
    std::stable_sort(sorted_messages.begin(), sorted_messages.end(),
                     [](const auto & a, const auto & b) {
                       return std::tie(a.first, a.second->topic_name) <
                              std::tie(b.first, b.second->topic_name);
                     });

    // Write messages
    for (const auto & [timestamp, msg_ptr] : sorted_messages) {
      auto bag_message = std::make_shared<rosbag2_storage::SerializedBagMessage>();
      bag_message->topic_name = msg_ptr->topic_name;
      setBagTimestamp(*bag_message, timestamp, 0);  // Receipt time on the ROS clock

      // Copy serialized data
      bag_message->serialized_data = std::make_shared<rcutils_uint8_array_t>();
      *bag_message->serialized_data = msg_ptr->data.get_rcl_serialized_message();

      writer.write(bag_message);
    }

    RCLCPP_INFO(logger_, "Wrote %zu messages to %s",
                sorted_messages.size(), job.output_path.c_str());

    return true;

  } catch (const std::exception & e) {
    RCLCPP_ERROR(logger_, "Exception while writing rosbag: %s", e.what());
    return false;
  }
}

void Recorder::setClipCallback(ClipCallback callback)
{
  std::lock_guard<std::mutex> lock(callback_mutex_);
  clip_callback_ = std::move(callback);
}

size_t Recorder::getActiveRecordingsCount() const
{
  return active_recordings_.load();
}

size_t Recorder::getCompletedRecordingsCount() const
{
  return completed_recordings_.load();
}

size_t Recorder::getFailedRecordingsCount() const
{
  return failed_recordings_.load();
}

size_t Recorder::getPendingClipsCount() const
{
  return trigger_queue_.pendingCount();
}

size_t Recorder::getActivePinsCount() const
{
  return pin_manager_.activeCount();
}

std::vector<std::string> Recorder::getActiveSnapshotNames() const
{
  return trigger_queue_.activeProfiles();
}

}  // namespace ros_time_machine
