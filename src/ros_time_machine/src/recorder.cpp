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
#include <filesystem>
#include <iomanip>
#include <sstream>
#include <thread>
#include <chrono>
#include <rosbag2_cpp/writers/sequential_writer.hpp>
#include <rosbag2_storage/storage_options.hpp>

namespace ros_time_machine
{

Recorder::Recorder(
  rclcpp::Node::SharedPtr node,
  rclcpp::Logger logger)
  : node_(node),
    logger_(logger),
    buffer_manager_(nullptr),
    shutdown_(false),
    active_recordings_(0),
    completed_recordings_(0),
    failed_recordings_(0),
    initialized_(false)
{
}

Recorder::~Recorder()
{
  // Signal shutdown
  shutdown_ = true;
  queue_cv_.notify_all();

  // Wait for all worker threads to finish
  for (auto & thread : worker_threads_) {
    if (thread.joinable()) {
      thread.join();
    }
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

  // Create worker threads
  size_t num_threads = config.global.recording_threads;
  RCLCPP_INFO(logger_, "Starting %zu recording worker threads", num_threads);

  for (size_t i = 0; i < num_threads; ++i) {
    worker_threads_.emplace_back(&Recorder::workerThread, this, i);
  }

  initialized_ = true;
  RCLCPP_INFO(logger_, "Recorder initialized successfully");

  return true;
}

void Recorder::handleTrigger(
  const TriggerEvent & event,
  const SnapshotConfig & snapshot_config)
{
  if (!initialized_) {
    RCLCPP_ERROR(logger_, "Recorder not initialized");
    return;
  }

  RCLCPP_INFO(logger_, "Handling trigger for snapshot '%s'", event.snapshot_name.c_str());

  // Create recording job
  RecordingJob job;
  job.snapshot_name = event.snapshot_name;
  job.trigger_event = event;
  job.snapshot_config = snapshot_config;
  job.status = RecordingStatus::PENDING;
  job.message_count = 0;

  // Calculate time range for recording
  job.start_time = event.trigger_time - rclcpp::Duration::from_seconds(
    snapshot_config.recording.pre_event_duration);
  job.end_time = event.trigger_time + rclcpp::Duration::from_seconds(
    snapshot_config.recording.post_event_duration);

  // Generate output path
  job.output_path = generateOutputPath(
    event.snapshot_name,
    event.trigger_time,
    snapshot_config.output);

  RCLCPP_INFO(logger_,
              "Recording job created: %s [%.2fs pre, %.2fs post]",
              job.output_path.c_str(),
              snapshot_config.recording.pre_event_duration,
              snapshot_config.recording.post_event_duration);

  // Add to queue
  {
    std::lock_guard<std::mutex> lock(queue_mutex_);
    job_queue_.push(job);
  }
  queue_cv_.notify_one();
}

void Recorder::workerThread(size_t thread_id)
{
  RCLCPP_DEBUG(logger_, "Worker thread %zu started", thread_id);

  while (!shutdown_) {
    RecordingJob job;

    // Wait for a job
    {
      std::unique_lock<std::mutex> lock(queue_mutex_);
      queue_cv_.wait(lock, [this] {
        return !job_queue_.empty() || shutdown_;
      });

      if (shutdown_ && job_queue_.empty()) {
        break;
      }

      if (!job_queue_.empty()) {
        job = job_queue_.front();
        job_queue_.pop();
      } else {
        continue;
      }
    }

    // Process the job
    active_recordings_++;
    processRecording(job);
    active_recordings_--;

    if (job.status == RecordingStatus::COMPLETED) {
      completed_recordings_++;
    } else if (job.status == RecordingStatus::FAILED) {
      failed_recordings_++;
    }
  }

  RCLCPP_DEBUG(logger_, "Worker thread %zu stopped", thread_id);
}

void Recorder::processRecording(RecordingJob & job)
{
  RCLCPP_INFO(logger_,
              "Processing recording for snapshot '%s' (thread processing)",
              job.snapshot_name.c_str());

  job.status = RecordingStatus::PRE_EVENT;

  // Wait for post-event duration to ensure all post-event messages have arrived
  double post_event_seconds = job.snapshot_config.recording.post_event_duration;

  RCLCPP_INFO(logger_,
              "Waiting %.2f seconds for post-event messages to arrive",
              post_event_seconds);

  auto wait_duration = std::chrono::duration<double>(post_event_seconds);
  std::this_thread::sleep_for(wait_duration);

  job.status = RecordingStatus::POST_EVENT;

  // Collect messages from buffer manager for all topics
  std::map<std::string, std::vector<SerializedMessage>> all_messages;

  for (const auto & topic : job.snapshot_config.recording.topics) {
    auto messages = buffer_manager_->getMessages(
      topic,
      job.start_time,
      job.end_time);

    if (!messages.empty()) {
      all_messages[topic] = messages;
      job.message_count += messages.size();
      RCLCPP_DEBUG(logger_, "Retrieved %zu messages from topic %s",
                   messages.size(), topic.c_str());
    } else {
      RCLCPP_WARN(logger_, "No messages found for topic %s in time range",
                  topic.c_str());
    }
  }

  if (all_messages.empty()) {
    RCLCPP_ERROR(logger_, "No messages retrieved for any topic, aborting recording");
    job.status = RecordingStatus::FAILED;
    return;
  }

  RCLCPP_INFO(logger_, "Retrieved %zu total messages from %zu topics",
              job.message_count, all_messages.size());

  // Write to rosbag
  job.status = RecordingStatus::WRITING;
  bool success = writeRosbag(job, all_messages);

  if (success) {
    job.status = RecordingStatus::COMPLETED;
    RCLCPP_INFO(logger_,
                "Recording completed successfully: %s (%zu messages)",
                job.output_path.c_str(),
                job.message_count);
  } else {
    job.status = RecordingStatus::FAILED;
    RCLCPP_ERROR(logger_, "Recording failed: %s", job.output_path.c_str());
  }
}

std::string Recorder::generateOutputPath(
  const std::string & snapshot_name,
  const rclcpp::Time & trigger_time,
  const OutputConfig & output_config)
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

  // Replace placeholders in pattern
  std::string filename = output_config.name_pattern;
  size_t pos = 0;
  while ((pos = filename.find("{snapshot_name}", pos)) != std::string::npos) {
    filename.replace(pos, 15, snapshot_name);
    pos += snapshot_name.length();
  }

  pos = 0;
  while ((pos = filename.find("{timestamp}", pos)) != std::string::npos) {
    filename.replace(pos, 11, timestamp);
    pos += timestamp.length();
  }

  return (base_path / filename).string();
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
    storage_options.storage_id = "mcap";  // Use MCAP format (modern, efficient)

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
    std::vector<std::pair<rclcpp::Time, const SerializedMessage *>> sorted_messages;

    for (const auto & [topic_name, topic_messages] : messages) {
      for (const auto & msg : topic_messages) {
        sorted_messages.emplace_back(msg.timestamp, &msg);
      }
    }

    // Sort by timestamp
    std::sort(sorted_messages.begin(), sorted_messages.end(),
              [](const auto & a, const auto & b) {
                return a.first < b.first;
              });

    // Write messages
    for (const auto & [timestamp, msg_ptr] : sorted_messages) {
      auto bag_message = std::make_shared<rosbag2_storage::SerializedBagMessage>();
      bag_message->topic_name = msg_ptr->topic_name;
      bag_message->send_timestamp = timestamp.nanoseconds();    // Message publish timestamp
      bag_message->recv_timestamp = timestamp.nanoseconds();    // Reception timestamp

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

}  // namespace ros_time_machine
