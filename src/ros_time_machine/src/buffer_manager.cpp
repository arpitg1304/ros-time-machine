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

#include "ros_time_machine/buffer_manager.hpp"
#include <algorithm>
#include <set>

namespace ros_time_machine
{

BufferManager::BufferManager(
  rclcpp::Node::SharedPtr node,
  rclcpp::Logger logger)
: node_(node), logger_(logger), initialized_(false), pin_budget_fraction_(0.0)
{
}

bool BufferManager::initialize(const TimeMachineConfig & config)
{
  RCLCPP_INFO(logger_, "Initializing Buffer Manager...");

  // Extract all unique topics and their required buffer durations
  required_topics_ = extractTopicsFromConfig(config);

  if (required_topics_.empty()) {
    RCLCPP_WARN(logger_, "No topics to buffer found in configuration");
    return false;
  }

  RCLCPP_INFO(logger_, "Found %zu unique topics to buffer", required_topics_.size());

  // Get all currently available topics and their types
  auto topic_names_and_types = node_->get_topic_names_and_types();

  // Create subscriptions for topics that are currently available
  size_t subscribed_count = 0;
  for (const auto & [topic_name, duration] : required_topics_) {
    RCLCPP_INFO(logger_, "Preparing to buffer topic: %s (duration: %.1fs)",
                topic_name.c_str(), duration);

    // Check if topic is currently available
    auto it = topic_names_and_types.find(topic_name);
    if (it == topic_names_and_types.end() || it->second.empty()) {
      RCLCPP_WARN(logger_, "Topic %s not currently available, will buffer when it appears",
                  topic_name.c_str());

      // Store placeholder info (will subscribe when topic appears)
      TopicInfo info;
      info.topic_name = topic_name;
      info.buffer_capacity = calculateBufferCapacity(duration, 50.0);  // Assume 50Hz for cameras
      info.message_size_estimate = 0;
      info.message_count_for_estimate = 0;

      std::lock_guard<std::mutex> lock(buffers_mutex_);
      topic_buffers_[topic_name] = info;
      continue;
    }

    // Get the message type (use first type if multiple)
    std::string type_name = it->second[0];

    // Create generic subscription
    auto callback = [this, topic_name](std::shared_ptr<rclcpp::SerializedMessage> msg) {
        this->genericCallback(topic_name, msg);
      };

    try {
      auto subscription = node_->create_generic_subscription(
        topic_name,
        type_name,
        rclcpp::QoS(10),
        callback);

      // Store subscription info
      TopicInfo info;
      info.topic_name = topic_name;
      info.type_name = type_name;
      info.subscription = subscription;
      info.buffer_capacity = calculateBufferCapacity(duration, 50.0);
      info.message_size_estimate = 0;
      info.message_count_for_estimate = 0;

      // Create buffer immediately
      info.buffer = makeBuffer(info.buffer_capacity);

      std::lock_guard<std::mutex> lock(buffers_mutex_);
      topic_buffers_[topic_name] = info;

      subscribed_count++;
      RCLCPP_INFO(logger_, "Subscribed to %s (type: %s, capacity: %zu)",
                  topic_name.c_str(), type_name.c_str(), info.buffer_capacity);
    } catch (const std::exception & e) {
      RCLCPP_ERROR(logger_, "Failed to subscribe to topic %s: %s",
                   topic_name.c_str(), e.what());

      // Store placeholder even if subscription fails
      TopicInfo info;
      info.topic_name = topic_name;
      info.type_name = type_name;
      info.buffer_capacity = calculateBufferCapacity(duration, 50.0);
      info.message_size_estimate = 0;
      info.message_count_for_estimate = 0;

      std::lock_guard<std::mutex> lock(buffers_mutex_);
      topic_buffers_[topic_name] = info;
    }
  }

  initialized_ = true;
  RCLCPP_INFO(logger_, "Buffer Manager initialized: %zu/%zu topics subscribed",
              subscribed_count, required_topics_.size());

  if (subscribed_count == 0) {
    RCLCPP_WARN(logger_, "No topics are currently available for buffering");
  }

  // Start discovery timer to find new topics
  discovery_timer_ = node_->create_wall_timer(
    std::chrono::milliseconds(500),
    std::bind(&BufferManager::discoveryTimerCallback, this));

  RCLCPP_INFO(logger_, "Dynamic topic discovery enabled (checking every 500ms)");

  return true;
}

std::vector<SerializedMessage> BufferManager::getMessages(
  const std::string & topic_name,
  const rclcpp::Time & start_time,
  const rclcpp::Time & end_time)
{
  // Copy the window outside buffers_mutex_ (the buffer has its own lock), so a large
  // clip read does not stall every topic's subscription callback
  std::shared_ptr<CircularBuffer<SerializedMessage>> buffer;
  {
    std::lock_guard<std::mutex> lock(buffers_mutex_);
    auto it = topic_buffers_.find(topic_name);
    if (it != topic_buffers_.end()) {
      buffer = it->second.buffer;
    }
  }
  if (!buffer) {
    RCLCPP_WARN(logger_, "No buffer found for topic: %s", topic_name.c_str());
    return {};
  }

  // Convert rclcpp::Time to system_clock::time_point
  auto start_tp = std::chrono::system_clock::time_point(
    std::chrono::nanoseconds(start_time.nanoseconds()));
  auto end_tp = std::chrono::system_clock::time_point(
    std::chrono::nanoseconds(end_time.nanoseconds()));

  auto timestamped_messages = buffer->getRange(start_tp, end_tp);

  std::vector<SerializedMessage> result;
  result.reserve(timestamped_messages.size());
  for (const auto & tm : timestamped_messages) {
    result.push_back(tm.data);
  }

  RCLCPP_DEBUG(logger_, "Retrieved %zu messages from topic %s (range: %.2f to %.2f)",
               result.size(), topic_name.c_str(),
               start_time.seconds(), end_time.seconds());

  return result;
}

std::vector<SerializedMessage> BufferManager::getRecentMessages(
  const std::string & topic_name,
  const rclcpp::Duration & duration)
{
  auto now = node_->now();
  auto start_time = now - duration;
  return getMessages(topic_name, start_time, now);
}

TopicBufferStats BufferManager::getTopicStats(const std::string & topic_name) const
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);

  auto it = topic_buffers_.find(topic_name);
  if (it == topic_buffers_.end() || !it->second.buffer) {
    return TopicBufferStats();  // Return empty stats
  }

  return statsFor(it->second);
}

TopicBufferStats BufferManager::statsFor(const TopicInfo & info) const
{
  TopicBufferStats stats;

  stats.topic_name = info.topic_name;
  stats.message_count = info.buffer->size();
  stats.capacity = info.buffer->capacity();
  stats.messages_dropped = info.buffer->droppedCount();
  stats.utilization_percent = (stats.capacity > 0) ?
    (100.0 * stats.message_count / stats.capacity) : 0.0;

  // Convert time points to rclcpp::Time
  auto oldest_tp = info.buffer->oldestTimestamp();
  auto newest_tp = info.buffer->newestTimestamp();

  stats.oldest_message_time = rclcpp::Time(
    std::chrono::duration_cast<std::chrono::nanoseconds>(
      oldest_tp.time_since_epoch()).count());
  stats.newest_message_time = rclcpp::Time(
    std::chrono::duration_cast<std::chrono::nanoseconds>(
      newest_tp.time_since_epoch()).count());

  stats.pinned_message_count = info.buffer->retainedCount();
  stats.estimated_memory_bytes =
    (stats.message_count + stats.pinned_message_count) * info.message_size_estimate;

  return stats;
}

std::vector<TopicBufferStats> BufferManager::getAllStats() const
{
  std::vector<TopicBufferStats> all_stats;

  std::lock_guard<std::mutex> lock(buffers_mutex_);
  for (const auto & [_, info] : topic_buffers_) {
    if (info.buffer) {
      all_stats.push_back(statsFor(info));
    }
  }

  return all_stats;
}

size_t BufferManager::getTotalMemoryUsage() const
{
  size_t total = 0;
  std::lock_guard<std::mutex> lock(buffers_mutex_);

  for (const auto & [_, info] : topic_buffers_) {
    if (info.buffer) {
      total += (info.buffer->size() + info.buffer->retainedCount()) * info.message_size_estimate;
    }
  }

  return total;
}

std::vector<std::string> BufferManager::getBufferedTopics() const
{
  std::vector<std::string> topics;
  std::lock_guard<std::mutex> lock(buffers_mutex_);

  for (const auto & [topic_name, _] : topic_buffers_) {
    topics.push_back(topic_name);
  }

  return topics;
}

bool BufferManager::isTopicBuffered(const std::string & topic_name) const
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);
  return topic_buffers_.find(topic_name) != topic_buffers_.end();
}

void BufferManager::setEvictionGuard(std::function<bool(int64_t)> guard, double budget_fraction)
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);
  eviction_guard_ = std::move(guard);
  pin_budget_fraction_ = budget_fraction;

  for (auto & [_, info] : topic_buffers_) {
    if (info.buffer) {
      applyEvictionGuard(*info.buffer);
    }
  }
}

size_t BufferManager::pruneRetained()
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);
  size_t released = 0;
  for (auto & [_, info] : topic_buffers_) {
    if (info.buffer) {
      released += info.buffer->pruneRetained();
    }
  }
  return released;
}

rclcpp::Time BufferManager::getOldestTimestamp(const std::string & topic_name) const
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);
  auto it = topic_buffers_.find(topic_name);
  if (it == topic_buffers_.end() || !it->second.buffer || it->second.buffer->empty()) {
    return rclcpp::Time(0, 0, RCL_ROS_TIME);
  }
  auto oldest = it->second.buffer->oldestTimestamp();
  return rclcpp::Time(
    std::chrono::duration_cast<std::chrono::nanoseconds>(oldest.time_since_epoch()).count(),
    RCL_ROS_TIME);
}

std::string BufferManager::getTopicType(const std::string & topic_name) const
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);
  auto it = topic_buffers_.find(topic_name);
  return it == topic_buffers_.end() ? std::string() : it->second.type_name;
}

std::shared_ptr<CircularBuffer<SerializedMessage>> BufferManager::makeBuffer(size_t capacity) const
{
  auto buffer = std::make_shared<CircularBuffer<SerializedMessage>>(capacity);
  applyEvictionGuard(*buffer);
  return buffer;
}

void BufferManager::applyEvictionGuard(CircularBuffer<SerializedMessage> & buffer) const
{
  auto max_retained = static_cast<size_t>(buffer.capacity() * pin_budget_fraction_);
  if (!eviction_guard_) {
    buffer.setEvictionGuard(nullptr, max_retained);
    return;
  }
  auto guard = eviction_guard_;
  buffer.setEvictionGuard(
    [guard](const CircularBuffer<SerializedMessage>::TimePoint & t) {
      return guard(
        std::chrono::duration_cast<std::chrono::nanoseconds>(t.time_since_epoch()).count());
    },
    max_retained);
}

bool BufferManager::createTopicBuffer(
  const std::string & topic_name,
  const std::string & type_name,
  double duration,
  double estimated_frequency)
{
  size_t capacity = calculateBufferCapacity(duration, estimated_frequency);

  RCLCPP_INFO(logger_, "Creating buffer for %s (type: %s, capacity: %zu messages)",
              topic_name.c_str(), type_name.c_str(), capacity);

  std::lock_guard<std::mutex> lock(buffers_mutex_);
  auto buffer = makeBuffer(capacity);

  auto it = topic_buffers_.find(topic_name);
  if (it != topic_buffers_.end()) {
    it->second.buffer = buffer;
    it->second.type_name = type_name;
    it->second.buffer_capacity = capacity;
    return true;
  }

  return false;
}

size_t BufferManager::calculateBufferCapacity(double duration, double frequency) const
{
  // Calculate based on duration and estimated frequency
  // Add 20% margin for safety
  size_t capacity = static_cast<size_t>(duration * frequency * 1.2);

  // Minimum capacity of 10 messages
  capacity = std::max(capacity, static_cast<size_t>(10));

  // Maximum capacity of 10000 messages per topic (safety limit)
  capacity = std::min(capacity, static_cast<size_t>(10000));

  return capacity;
}

void BufferManager::genericCallback(
  const std::string & topic_name,
  std::shared_ptr<rclcpp::SerializedMessage> serialized_msg)
{
  std::lock_guard<std::mutex> lock(buffers_mutex_);

  auto it = topic_buffers_.find(topic_name);
  if (it == topic_buffers_.end()) {
    return;  // Topic not in our list (shouldn't happen)
  }

  auto & info = it->second;

  // Lazy initialization: create buffer on first message
  if (!info.buffer) {
    // We don't know the exact type, but we can get it from the subscription
    // For now, just create the buffer with the configured capacity
    info.buffer = makeBuffer(info.buffer_capacity);
    RCLCPP_INFO(logger_, "Created buffer for topic %s on first message (capacity: %zu)",
                topic_name.c_str(), info.buffer_capacity);
  }

  // Create SerializedMessage with timestamp
  auto now = node_->now();
  SerializedMessage msg(*serialized_msg, topic_name, info.type_name, now);

  // Update message size estimate
  updateMessageSizeEstimate(topic_name, serialized_msg->size());

  // Add to buffer using ROS time (not wall clock time)
  // This ensures time range queries work correctly with ROS timestamps
  auto timestamp = std::chrono::system_clock::time_point(
    std::chrono::nanoseconds(now.nanoseconds()));
  bool added = info.buffer->push(timestamp, msg);

  if (!added) {
    RCLCPP_DEBUG_THROTTLE(logger_, *node_->get_clock(), 5000,
                          "Buffer overflow for topic %s (oldest message dropped)",
                          topic_name.c_str());
  }
}

void BufferManager::updateMessageSizeEstimate(
  const std::string & topic_name,
  size_t message_size)
{
  auto it = topic_buffers_.find(topic_name);
  if (it == topic_buffers_.end()) {
    return;
  }

  auto & info = it->second;

  // Running average
  if (info.message_count_for_estimate == 0) {
    info.message_size_estimate = message_size;
    info.message_count_for_estimate = 1;
  } else {
    // Exponential moving average (weight recent messages more)
    const double alpha = 0.1;  // Smoothing factor
    info.message_size_estimate = static_cast<size_t>(
      alpha * message_size + (1.0 - alpha) * info.message_size_estimate);
    info.message_count_for_estimate++;
  }
}

std::map<std::string, double> BufferManager::extractTopicsFromConfig(
  const TimeMachineConfig & config)
{
  std::map<std::string, double> topics;  // topic_name -> max_buffer_duration

  // Iterate through all snapshots
  for (const auto & snapshot : config.snapshots) {
    // Buffer needs to hold pre + post event data since messages continue arriving after trigger,
    // plus however late a time-addressed trigger may arrive (VLM detection lag)
    double duration = snapshot.recording.pre_event_duration +
      snapshot.recording.post_event_duration +
      config.global.max_trigger_latency;

    // Add all recording topics from this snapshot
    for (const auto & topic : snapshot.recording.topics) {
      // Keep the maximum duration required for each topic
      auto it = topics.find(topic);
      if (it == topics.end()) {
        topics[topic] = duration;
      } else {
        topics[topic] = std::max(topics[topic], duration);
      }
    }
  }

  return topics;
}

void BufferManager::discoveryTimerCallback()
{
  if (!initialized_) {
    return;
  }

  // Get all currently available topics and their types
  auto topic_names_and_types = node_->get_topic_names_and_types();

  std::lock_guard<std::mutex> lock(buffers_mutex_);

  // Check each required topic
  for (const auto & [topic_name, duration] : required_topics_) {
    // Skip if we already have a subscription for this topic
    auto it = topic_buffers_.find(topic_name);
    if (it != topic_buffers_.end() && it->second.subscription) {
      continue;  // Already subscribed
    }

    // Check if topic is now available
    auto topic_it = topic_names_and_types.find(topic_name);
    if (topic_it == topic_names_and_types.end() || topic_it->second.empty()) {
      continue;  // Still not available
    }

    // Get the message type (use first type if multiple)
    std::string type_name = topic_it->second[0];

    // Create generic subscription
    auto callback = [this, topic_name](std::shared_ptr<rclcpp::SerializedMessage> msg) {
        this->genericCallback(topic_name, msg);
      };

    try {
      auto subscription = node_->create_generic_subscription(
        topic_name,
        type_name,
        rclcpp::QoS(10),
        callback);

      // Update or create TopicInfo
      TopicInfo info;
      info.topic_name = topic_name;
      info.type_name = type_name;
      info.subscription = subscription;
      info.buffer_capacity = calculateBufferCapacity(duration, 50.0);
      info.message_size_estimate = 0;
      info.message_count_for_estimate = 0;

      // Create buffer
      info.buffer = makeBuffer(info.buffer_capacity);

      topic_buffers_[topic_name] = info;

      RCLCPP_INFO(logger_,
          "Dynamically discovered and subscribed to topic %s (type: %s, capacity: %zu)",
                  topic_name.c_str(), type_name.c_str(), info.buffer_capacity);
    } catch (const std::exception & e) {
      RCLCPP_ERROR(logger_, "Failed to subscribe to topic %s: %s",
                   topic_name.c_str(), e.what());
    }
  }
}

}  // namespace ros_time_machine
