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

#ifndef ROS_TIME_MACHINE__TRIGGER_MONITOR_HPP_
#define ROS_TIME_MACHINE__TRIGGER_MONITOR_HPP_

#include <string>
#include <memory>
#include <map>
#include <mutex>
#include <functional>
#include <chrono>
#include <deque>

#include "rclcpp/rclcpp.hpp"
#include "ros_time_machine/config_types.hpp"

namespace ros_time_machine
{

/**
 * @brief Information about a triggered event
 */
struct TriggerEvent
{
  std::string snapshot_name;
  rclcpp::Time trigger_time;
  std::string trigger_topic;
  double trigger_value;
};

/**
 * @brief Callback type for when a trigger event occurs
 */
using TriggerCallback = std::function<void(const TriggerEvent &)>;

/**
 * @brief Rate limiter for a specific snapshot
 */
struct RateLimiter
{
  size_t max_triggers_per_minute;
  std::deque<std::chrono::steady_clock::time_point> trigger_times;

  /**
   * @brief Check if a new trigger is allowed
   * @return true if trigger is allowed, false if rate limited
   */
  bool allowTrigger();
};

/**
 * @brief Monitor trigger topics and evaluate conditions
 *
 * This class subscribes to trigger topics specified in the configuration
 * and evaluates conditions on incoming messages. When a condition is met,
 * it invokes a callback to initiate recording.
 */
class TriggerMonitor
{
public:
  /**
   * @brief Construct a new Trigger Monitor
   * @param node Shared pointer to the ROS node
   * @param logger Logger for this component
   */
  TriggerMonitor(
    rclcpp::Node::SharedPtr node,
    rclcpp::Logger logger);

  /**
   * @brief Initialize the trigger monitor with configuration
   * @param config The time machine configuration
   * @param callback Callback to invoke when a trigger occurs
   * @return true if initialization successful
   */
  bool initialize(
    const TimeMachineConfig & config,
    TriggerCallback callback);

  /**
   * @brief Get statistics for a specific snapshot's triggers
   * @param snapshot_name Name of the snapshot
   * @return Number of times this snapshot has been triggered
   */
  size_t getTriggerCount(const std::string & snapshot_name) const;

  /**
   * @brief Get total number of triggers across all snapshots
   * @return Total trigger count
   */
  size_t getTotalTriggerCount() const;

  /**
   * @brief Check if a snapshot is currently rate limited
   * @param snapshot_name Name of the snapshot
   * @return true if rate limited
   */
  bool isRateLimited(const std::string & snapshot_name) const;

private:
  /**
   * @brief Information about a trigger subscription
   */
  struct TriggerInfo
  {
    std::string snapshot_name;
    std::string topic_name;
    std::string type_name;
    TriggerConfig trigger_config;
    rclcpp::GenericSubscription::SharedPtr subscription;
    RateLimiter rate_limiter;
    size_t trigger_count;
  };

  /**
   * @brief Generic callback for trigger topics
   * @param snapshot_name Name of the snapshot this trigger belongs to
   * @param msg Serialized message
   */
  void triggerCallback(
    const std::string & snapshot_name,
    std::shared_ptr<rclcpp::SerializedMessage> msg);

  /**
   * @brief Evaluate a condition on a message
   * @param msg Serialized message
   * @param trigger_config Trigger configuration with condition
   * @param value_out Output parameter for the extracted value
   * @return true if condition is met
   */
  bool evaluateCondition(
    const std::shared_ptr<rclcpp::SerializedMessage> & msg,
    const TriggerConfig & trigger_config,
    double & value_out);

  /**
   * @brief Extract a numeric value from a serialized message
   * @param msg Serialized message
   * @param field_name Name of the field to extract
   * @param type_name Message type name
   * @return Extracted value as double
   */
  double extractFieldValue(
    const std::shared_ptr<rclcpp::SerializedMessage> & msg,
    const std::string & field_name,
    const std::string & type_name);

  /**
   * @brief Compare two values using an operator
   * @param value Actual value from message
   * @param expected Expected value from condition
   * @param op Comparison operator
   * @return true if comparison passes
   */
  bool compareValues(double value, double expected, ComparisonOperator op);

  /**
   * @brief Timer callback to discover new trigger topics
   */
  void discoveryTimerCallback();

  rclcpp::Node::SharedPtr node_;
  rclcpp::Logger logger_;
  TriggerCallback trigger_callback_;

  std::map<std::string, TriggerInfo> triggers_;
  mutable std::mutex triggers_mutex_;

  TimeMachineConfig config_;
  rclcpp::TimerBase::SharedPtr discovery_timer_;

  bool initialized_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__TRIGGER_MONITOR_HPP_
