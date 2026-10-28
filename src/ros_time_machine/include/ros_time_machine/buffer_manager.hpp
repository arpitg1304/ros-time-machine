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

#ifndef ROS_TIME_MACHINE__BUFFER_MANAGER_HPP_
#define ROS_TIME_MACHINE__BUFFER_MANAGER_HPP_

#include <string>
#include <memory>
#include <map>
#include <vector>
#include <chrono>
#include <functional>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/serialization.hpp"
#include "rclcpp/serialized_message.hpp"
#include "rosbag2_storage/serialized_bag_message.hpp"

#include "ros_time_machine/circular_buffer.hpp"
#include "ros_time_machine/config_types.hpp"

namespace ros_time_machine
{

/**
 * @brief Stores a serialized ROS message with metadata
 */
struct SerializedMessage
{
  rclcpp::SerializedMessage data;
  std::string topic_name;
  std::string type_name;
  rclcpp::Time timestamp;

  SerializedMessage() = default;

  SerializedMessage(
    const rclcpp::SerializedMessage & msg,
    const std::string & topic,
    const std::string & type,
    const rclcpp::Time & ts)
    : data(msg), topic_name(topic), type_name(type), timestamp(ts)
  {
  }
};

/**
 * @brief Statistics for a single topic buffer
 */
struct TopicBufferStats
{
  std::string topic_name;
  size_t message_count;
  size_t capacity;
  uint64_t messages_dropped;
  double utilization_percent;
  rclcpp::Time oldest_message_time;
  rclcpp::Time newest_message_time;
  size_t estimated_memory_bytes;
  size_t pinned_message_count;  // Held past eviction for pins / pending triggers
};

/**
 * @brief Manages circular buffers for all topics
 *
 * The BufferManager is responsible for:
 * - Creating and maintaining circular buffers for all configured topics
 * - Subscribing to topics using generic subscriptions
 * - Storing serialized messages with timestamps
 * - Providing access to buffered messages within time ranges
 * - Tracking buffer statistics
 */
class BufferManager
{
public:
  /**
   * @brief Construct a new Buffer Manager
   *
   * @param node ROS2 node for creating subscriptions
   * @param logger Logger for diagnostic messages
   */
  BufferManager(
    rclcpp::Node::SharedPtr node,
    rclcpp::Logger logger);

  /**
   * @brief Initialize buffers based on configuration
   *
   * @param config Time machine configuration
   * @return true if initialization succeeded
   */
  bool initialize(const TimeMachineConfig & config);

  /**
   * @brief Get buffered messages for a topic within a time range
   *
   * @param topic_name Name of the topic
   * @param start_time Start of time range
   * @param end_time End of time range
   * @return std::vector<SerializedMessage> Messages within the range
   */
  std::vector<SerializedMessage> getMessages(
    const std::string & topic_name,
    const rclcpp::Time & start_time,
    const rclcpp::Time & end_time);

  /**
   * @brief Get recent messages for a topic
   *
   * @param topic_name Name of the topic
   * @param duration How far back to retrieve
   * @return std::vector<SerializedMessage> Recent messages
   */
  std::vector<SerializedMessage> getRecentMessages(
    const std::string & topic_name,
    const rclcpp::Duration & duration);

  /**
   * @brief Get buffer statistics for a specific topic
   *
   * @param topic_name Name of the topic
   * @return TopicBufferStats Statistics for the topic, or empty if not found
   */
  TopicBufferStats getTopicStats(const std::string & topic_name) const;

  /**
   * @brief Get buffer statistics for all topics
   *
   * @return std::vector<TopicBufferStats> Statistics for all topics
   */
  std::vector<TopicBufferStats> getAllStats() const;

  /**
   * @brief Get total memory usage across all buffers
   *
   * @return size_t Estimated total memory in bytes
   */
  size_t getTotalMemoryUsage() const;

  /**
   * @brief Get list of all buffered topics
   *
   * @return std::vector<std::string> List of topic names
   */
  std::vector<std::string> getBufferedTopics() const;

  /**
   * @brief Check if a topic is being buffered
   *
   * @param topic_name Name of the topic
   * @return true if topic is buffered
   */
  bool isTopicBuffered(const std::string & topic_name) const;

  /**
   * @brief Protect messages from eviction while guard(stamp_ns) returns true
   *
   * Applies to every existing buffer and to buffers created later by topic
   * discovery. The guard runs under buffer locks and must not call back into
   * the BufferManager.
   *
   * @param guard Predicate over message ROS time in nanoseconds
   * @param budget_fraction Share of each buffer's capacity that may be retained
   */
  void setEvictionGuard(std::function<bool(int64_t)> guard, double budget_fraction);

  /**
   * @brief Release retained messages the guard no longer covers
   *
   * @return size_t Number of messages released across all topics
   */
  size_t pruneRetained();

  /**
   * @brief ROS time of the oldest message still held for a topic
   *
   * @return rclcpp::Time Oldest stamp, or zero if the topic has no data
   */
  rclcpp::Time getOldestTimestamp(const std::string & topic_name) const;

  /**
   * @brief Message type of a buffered topic ("" until discovered)
   */
  std::string getTopicType(const std::string & topic_name) const;

private:
  /**
   * @brief Information about a buffered topic
   */
  struct TopicInfo
  {
    std::string topic_name;
    std::string type_name;
    size_t buffer_capacity;
    std::shared_ptr<CircularBuffer<SerializedMessage>> buffer;
    rclcpp::GenericSubscription::SharedPtr subscription;
    size_t message_size_estimate;  // Running average of message sizes
    size_t message_count_for_estimate;
  };

  /**
   * @brief Create a buffer for a specific topic
   *
   * @param topic_name Name of the topic
   * @param type_name Message type name
   * @param duration Buffer duration in seconds
   * @param estimated_frequency Estimated message frequency (Hz)
   * @return true if buffer was created successfully
   */
  bool createTopicBuffer(
    const std::string & topic_name,
    const std::string & type_name,
    double duration,
    double estimated_frequency = 10.0);

  /**
   * @brief Calculate buffer capacity based on duration and frequency
   *
   * @param duration Buffer duration in seconds
   * @param frequency Estimated message frequency in Hz
   * @return size_t Buffer capacity (number of messages)
   */
  size_t calculateBufferCapacity(double duration, double frequency) const;

  /**
   * @brief Callback for generic subscription
   *
   * @param topic_name Name of the topic
   * @param serialized_msg Serialized message data
   */
  void genericCallback(
    const std::string & topic_name,
    std::shared_ptr<rclcpp::SerializedMessage> serialized_msg);

  /**
   * @brief Update message size estimate for a topic
   *
   * @param topic_name Name of the topic
   * @param message_size Size of the message in bytes
   */
  void updateMessageSizeEstimate(
    const std::string & topic_name,
    size_t message_size);

  /**
   * @brief Get all unique topics from configuration
   *
   * @param config Configuration to extract topics from
   * @return std::map<std::string, double> Map of topic name to required buffer duration
   */
  std::map<std::string, double> extractTopicsFromConfig(
    const TimeMachineConfig & config);

  /**
   * @brief Timer callback to discover new topics
   */
  void discoveryTimerCallback();

  /**
   * @brief Create a topic buffer with the current eviction guard applied
   */
  std::shared_ptr<CircularBuffer<SerializedMessage>> makeBuffer(size_t capacity) const;

  /**
   * @brief (Re)apply the eviction guard and pin budget to one buffer
   */
  void applyEvictionGuard(CircularBuffer<SerializedMessage> & buffer) const;

  /**
   * @brief Fill statistics for one topic (caller holds buffers_mutex_)
   */
  TopicBufferStats statsFor(const TopicInfo & info) const;

  rclcpp::Node::SharedPtr node_;
  rclcpp::Logger logger_;
  std::map<std::string, TopicInfo> topic_buffers_;
  mutable std::mutex buffers_mutex_;
  bool initialized_;

  // Topic discovery
  std::map<std::string, double> required_topics_;  // topic_name -> buffer_duration
  rclcpp::TimerBase::SharedPtr discovery_timer_;

  // Eviction protection for pinned ranges and pending triggers
  std::function<bool(int64_t)> eviction_guard_;
  double pin_budget_fraction_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__BUFFER_MANAGER_HPP_
