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

#include "ros_time_machine/trigger_monitor.hpp"
#include <rclcpp/serialization.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/float64.hpp>
#include <sensor_msgs/msg/imu.hpp>

namespace ros_time_machine
{

bool RateLimiter::allowTrigger()
{
  auto now = std::chrono::steady_clock::now();
  auto one_minute_ago = now - std::chrono::minutes(1);

  // Remove triggers older than 1 minute
  while (!trigger_times.empty() && trigger_times.front() < one_minute_ago) {
    trigger_times.pop_front();
  }

  // Check if we're at the limit
  if (trigger_times.size() >= max_triggers_per_minute) {
    return false;
  }

  // Allow trigger and record the time
  trigger_times.push_back(now);
  return true;
}

TriggerMonitor::TriggerMonitor(
  rclcpp::Node::SharedPtr node,
  rclcpp::Logger logger)
: node_(node), logger_(logger), initialized_(false)
{
}

bool TriggerMonitor::initialize(
  const TimeMachineConfig & config,
  TriggerCallback callback)
{
  RCLCPP_INFO(logger_, "Initializing Trigger Monitor...");

  if (!callback) {
    RCLCPP_ERROR(logger_, "Trigger callback is null");
    return false;
  }

  trigger_callback_ = callback;
  config_ = config;

  // Get all currently available topics and their types
  auto topic_names_and_types = node_->get_topic_names_and_types();

  // Create trigger subscriptions for each snapshot
  size_t subscribed_count = 0;
  size_t rule_count = 0;
  for (const auto & snapshot : config.snapshots) {
    const std::string & topic_name = snapshot.trigger.topic;

    if (!snapshot.trigger.enabled()) {
      RCLCPP_INFO(logger_, "Snapshot '%s' has no rule trigger (EventTrigger / service only)",
                  snapshot.name.c_str());
      continue;
    }
    rule_count++;

    RCLCPP_INFO(logger_, "Setting up trigger for snapshot '%s' on topic %s",
                snapshot.name.c_str(), topic_name.c_str());

    // Check if topic is currently available
    auto it = topic_names_and_types.find(topic_name);
    if (it == topic_names_and_types.end() || it->second.empty()) {
      RCLCPP_WARN(logger_, "Trigger topic %s not currently available for snapshot '%s'",
                  topic_name.c_str(), snapshot.name.c_str());
      continue;
    }

    // Get the message type (use first type if multiple)
    std::string type_name = it->second[0];

    // Verify it matches configured type
    if (type_name != snapshot.trigger.msg_type) {
      RCLCPP_WARN(logger_,
                  "Topic %s has type %s but config specifies %s. Using actual type.",
                  topic_name.c_str(), type_name.c_str(),
                  snapshot.trigger.msg_type.c_str());
    }

    // Create generic subscription
    auto callback_fn = [this, snapshot_name = snapshot.name]
      (std::shared_ptr<rclcpp::SerializedMessage> msg) {
        this->triggerCallback(snapshot_name, msg);
      };

    try {
      auto subscription = node_->create_generic_subscription(
        topic_name,
        type_name,
        rclcpp::QoS(10),
        callback_fn);

      // Store trigger info
      TriggerInfo info;
      info.snapshot_name = snapshot.name;
      info.topic_name = topic_name;
      info.type_name = type_name;
      info.trigger_config = snapshot.trigger;
      info.subscription = subscription;
      info.rate_limiter.max_triggers_per_minute =
        snapshot.rate_limit.max_triggers_per_minute;
      info.trigger_count = 0;

      std::lock_guard<std::mutex> lock(triggers_mutex_);
      triggers_[snapshot.name] = info;

      subscribed_count++;
      RCLCPP_INFO(logger_, "Subscribed to trigger topic %s (type: %s) for snapshot '%s'",
                  topic_name.c_str(), type_name.c_str(), snapshot.name.c_str());
    } catch (const std::exception & e) {
      RCLCPP_ERROR(logger_, "Failed to subscribe to trigger topic %s: %s",
                   topic_name.c_str(), e.what());
    }
  }

  initialized_ = true;
  RCLCPP_INFO(logger_, "Trigger Monitor initialized: %zu/%zu triggers active",
              subscribed_count, rule_count);

  if (subscribed_count == 0 && rule_count > 0) {
    RCLCPP_WARN(logger_, "No trigger topics are currently available");
  }

  // Start discovery timer to find new trigger topics
  discovery_timer_ = node_->create_wall_timer(
    std::chrono::milliseconds(500),
    std::bind(&TriggerMonitor::discoveryTimerCallback, this));

  RCLCPP_INFO(logger_, "Dynamic topic discovery enabled (checking every 500ms)");

  return true;
}

void TriggerMonitor::triggerCallback(
  const std::string & snapshot_name,
  std::shared_ptr<rclcpp::SerializedMessage> msg)
{
  RCLCPP_DEBUG(logger_, "Received message on trigger for snapshot '%s'", snapshot_name.c_str());

  std::lock_guard<std::mutex> lock(triggers_mutex_);

  auto it = triggers_.find(snapshot_name);
  if (it == triggers_.end()) {
    RCLCPP_WARN(logger_, "Snapshot '%s' not found in triggers map", snapshot_name.c_str());
    return;
  }

  auto & info = it->second;

  // Evaluate condition
  double extracted_value = 0.0;
  bool condition_met = evaluateCondition(msg, info.trigger_config, extracted_value);

  RCLCPP_DEBUG(logger_, "Condition evaluation for '%s': value=%.2f, met=%d",
               snapshot_name.c_str(), extracted_value, condition_met);

  if (!condition_met) {
    return;  // Condition not met, ignore
  }

  // Check rate limiting
  if (!info.rate_limiter.allowTrigger()) {
    RCLCPP_DEBUG_THROTTLE(logger_, *node_->get_clock(), 5000,
                          "Snapshot '%s' is rate limited (max %zu triggers/min)",
                          snapshot_name.c_str(),
                          info.rate_limiter.max_triggers_per_minute);
    return;
  }

  // Trigger event!
  info.trigger_count++;

  TriggerEvent event;
  event.snapshot_name = snapshot_name;
  event.trigger_time = node_->now();
  event.trigger_topic = info.topic_name;
  event.trigger_value = extracted_value;

  RCLCPP_INFO(logger_, "Trigger activated for snapshot '%s' (value: %.2f, count: %zu)",
              snapshot_name.c_str(), extracted_value, info.trigger_count);

  // Invoke callback
  trigger_callback_(event);
}

bool TriggerMonitor::evaluateCondition(
  const std::shared_ptr<rclcpp::SerializedMessage> & msg,
  const TriggerConfig & trigger_config,
  double & value_out)
{
  try {
    // Extract field value from message
    value_out = extractFieldValue(
      msg,
      trigger_config.condition.field,
      trigger_config.msg_type);

    // Compare with expected value
    return compareValues(
      value_out,
      trigger_config.condition.value,
      trigger_config.condition.op);

  } catch (const std::exception & e) {
    RCLCPP_DEBUG(logger_, "Failed to evaluate condition: %s", e.what());
    return false;
  }
}

double TriggerMonitor::extractFieldValue(
  const std::shared_ptr<rclcpp::SerializedMessage> & msg,
  const std::string & field_name,
  const std::string & type_name)
{
  // Handle common message types
  rclcpp::Serialization<std_msgs::msg::Bool> bool_serializer;
  rclcpp::Serialization<std_msgs::msg::Float64> float_serializer;
  rclcpp::Serialization<sensor_msgs::msg::Imu> imu_serializer;

  if (type_name == "std_msgs/msg/Bool") {
    std_msgs::msg::Bool bool_msg;
    bool_serializer.deserialize_message(msg.get(), &bool_msg);

    if (field_name == "data") {
      return bool_msg.data ? 1.0 : 0.0;
    }
  } else if (type_name == "std_msgs/msg/Float64") {
    std_msgs::msg::Float64 float_msg;
    float_serializer.deserialize_message(msg.get(), &float_msg);

    if (field_name == "data") {
      return float_msg.data;
    }
  } else if (type_name == "sensor_msgs/msg/Imu") {
    sensor_msgs::msg::Imu imu_msg;
    imu_serializer.deserialize_message(msg.get(), &imu_msg);

    // Support accessing nested fields with dot notation
    if (field_name == "linear_acceleration.x") {
      return imu_msg.linear_acceleration.x;
    } else if (field_name == "linear_acceleration.y") {
      return imu_msg.linear_acceleration.y;
    } else if (field_name == "linear_acceleration.z") {
      return imu_msg.linear_acceleration.z;
    } else if (field_name == "angular_velocity.x") {
      return imu_msg.angular_velocity.x;
    } else if (field_name == "angular_velocity.y") {
      return imu_msg.angular_velocity.y;
    } else if (field_name == "angular_velocity.z") {
      return imu_msg.angular_velocity.z;
    }
  }

  throw std::runtime_error(
    "Unsupported message type or field: " + type_name + " / " + field_name);
}

bool TriggerMonitor::compareValues(
  double value,
  double expected,
  ComparisonOperator op)
{
  const double epsilon = 1e-9;  // For floating point comparison

  switch (op) {
    case ComparisonOperator::EQUAL:
      return std::abs(value - expected) < epsilon;

    case ComparisonOperator::NOT_EQUAL:
      return std::abs(value - expected) >= epsilon;

    case ComparisonOperator::GREATER:
      return value > expected;

    case ComparisonOperator::LESS:
      return value < expected;

    case ComparisonOperator::GREATER_EQUAL:
      return value >= expected || std::abs(value - expected) < epsilon;

    case ComparisonOperator::LESS_EQUAL:
      return value <= expected || std::abs(value - expected) < epsilon;

    default:
      return false;
  }
}

size_t TriggerMonitor::getTriggerCount(const std::string & snapshot_name) const
{
  std::lock_guard<std::mutex> lock(triggers_mutex_);

  auto it = triggers_.find(snapshot_name);
  if (it != triggers_.end()) {
    return it->second.trigger_count;
  }
  return 0;
}

size_t TriggerMonitor::getTotalTriggerCount() const
{
  std::lock_guard<std::mutex> lock(triggers_mutex_);

  size_t total = 0;
  for (const auto & [_, info] : triggers_) {
    total += info.trigger_count;
  }
  return total;
}

bool TriggerMonitor::isRateLimited(const std::string & snapshot_name) const
{
  std::lock_guard<std::mutex> lock(triggers_mutex_);

  auto it = triggers_.find(snapshot_name);
  if (it == triggers_.end()) {
    return false;
  }

  const auto & limiter = it->second.rate_limiter;
  auto now = std::chrono::steady_clock::now();
  auto one_minute_ago = now - std::chrono::minutes(1);

  // Count recent triggers
  size_t recent_count = 0;
  for (const auto & time : limiter.trigger_times) {
    if (time >= one_minute_ago) {
      recent_count++;
    }
  }

  return recent_count >= limiter.max_triggers_per_minute;
}

void TriggerMonitor::discoveryTimerCallback()
{
  if (!initialized_) {
    return;
  }

  // Get all currently available topics and their types
  auto topic_names_and_types = node_->get_topic_names_and_types();

  std::lock_guard<std::mutex> lock(triggers_mutex_);

  // Check each snapshot's trigger topic
  for (const auto & snapshot : config_.snapshots) {
    const std::string & topic_name = snapshot.trigger.topic;

    if (!snapshot.trigger.enabled()) {
      continue;
    }

    // Skip if we already have a subscription for this snapshot
    if (triggers_.find(snapshot.name) != triggers_.end() &&
      triggers_[snapshot.name].subscription)
    {
      continue;
    }

    // Check if topic is now available
    auto it = topic_names_and_types.find(topic_name);
    if (it == topic_names_and_types.end() || it->second.empty()) {
      continue;  // Still not available
    }

    // Get the message type (use first type if multiple)
    std::string type_name = it->second[0];

    // Verify it matches configured type
    if (type_name != snapshot.trigger.msg_type) {
      RCLCPP_WARN(logger_,
                  "Topic %s has type %s but config specifies %s. Using actual type.",
                  topic_name.c_str(), type_name.c_str(),
                  snapshot.trigger.msg_type.c_str());
    }

    // Create generic subscription
    auto callback_fn = [this, snapshot_name = snapshot.name]
      (std::shared_ptr<rclcpp::SerializedMessage> msg) {
        this->triggerCallback(snapshot_name, msg);
      };

    try {
      auto subscription = node_->create_generic_subscription(
        topic_name,
        type_name,
        rclcpp::QoS(10),
        callback_fn);

      // Store trigger info
      TriggerInfo info;
      info.snapshot_name = snapshot.name;
      info.topic_name = topic_name;
      info.type_name = type_name;
      info.trigger_config = snapshot.trigger;
      info.subscription = subscription;
      info.rate_limiter.max_triggers_per_minute =
        snapshot.rate_limit.max_triggers_per_minute;
      info.trigger_count = 0;

      triggers_[snapshot.name] = info;

      RCLCPP_INFO(logger_,
          "Dynamically discovered and subscribed to trigger topic %s (type: %s) for snapshot '%s'",
                  topic_name.c_str(), type_name.c_str(), snapshot.name.c_str());
    } catch (const std::exception & e) {
      RCLCPP_ERROR(logger_, "Failed to subscribe to trigger topic %s: %s",
                   topic_name.c_str(), e.what());
    }
  }
}

}  // namespace ros_time_machine
