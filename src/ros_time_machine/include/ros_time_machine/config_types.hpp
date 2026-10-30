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

#ifndef ROS_TIME_MACHINE__CONFIG_TYPES_HPP_
#define ROS_TIME_MACHINE__CONFIG_TYPES_HPP_

#include <cstdint>
#include <map>
#include <string>
#include <vector>
#include <memory>

namespace ros_time_machine
{

/**
 * @brief Comparison operators for trigger conditions
 */
enum class ComparisonOperator
{
  EQUAL,           // ==
  NOT_EQUAL,       // !=
  GREATER,         // >
  GREATER_EQUAL,   // >=
  LESS,            // <
  LESS_EQUAL       // <=
};

/**
 * @brief Convert string to ComparisonOperator
 */
ComparisonOperator stringToOperator(const std::string & op_str);

/**
 * @brief Trigger condition configuration
 */
struct TriggerCondition
{
  std::string field;              // Field path in message (e.g., "data" or "pose.position.x")
  ComparisonOperator op;          // Comparison operator
  double value;                   // Value to compare against

  TriggerCondition()
  : field(""), op(ComparisonOperator::EQUAL), value(0.0) {}
};

/**
 * @brief Trigger configuration
 *
 * Optional: a snapshot without a trigger is a "profile" that only fires from
 * time-addressed EventTrigger messages, pins and the TriggerSnapshot service.
 */
struct TriggerConfig
{
  std::string topic;              // Topic name to subscribe to
  std::string msg_type;           // Message type (e.g., "std_msgs/msg/Bool")
  TriggerCondition condition;     // Condition to evaluate

  TriggerConfig()
  : topic(""), msg_type("") {}

  bool enabled() const {return !topic.empty();}
};

/**
 * @brief Recording configuration
 */
struct RecordingConfig
{
  std::vector<std::string> topics;     // List of topics to record
  double pre_event_duration;           // Seconds before trigger (N)
  double post_event_duration;          // Seconds after trigger (M)
  uint64_t max_buffer_size;            // Max buffer size per topic in MB

  RecordingConfig()
  : pre_event_duration(0.0),
    post_event_duration(0.0),
    max_buffer_size(100) {}
};

/**
 * @brief Rate limiting configuration
 */
struct RateLimitConfig
{
  uint32_t max_triggers_per_minute;    // Maximum triggers allowed per minute

  RateLimitConfig()
  : max_triggers_per_minute(10) {}
};

/**
 * @brief Output configuration
 */
struct OutputConfig
{
  std::string path;                    // Output directory path
  std::string name_pattern;            // Filename pattern ({snapshot_name}, {timestamp}, {label})
  std::string format;                  // Rosbag format ("mcap" or "sqlite3")

  OutputConfig()
  : path("/tmp/ros_time_machine"),
    name_pattern("{snapshot_name}_{timestamp}"),
    format("mcap") {}
};

/**
 * @brief What happens to a pin of this profile when nobody decides in time
 */
struct PinConfig
{
  bool keep_on_timeout;                // true for safety-critical profiles
  double default_ttl;                  // Seconds, used when PinRange gives ttl <= 0

  PinConfig()
  : keep_on_timeout(false),
    default_ttl(30.0) {}
};

/**
 * @brief Complete snapshot configuration
 */
struct SnapshotConfig
{
  std::string name;                    // Snapshot name (unique identifier)
  std::string description;             // Human-readable description
  TriggerConfig trigger;               // Trigger configuration
  RecordingConfig recording;           // Recording configuration
  RateLimitConfig rate_limit;          // Rate limiting configuration
  OutputConfig output;                 // Output configuration
  PinConfig pins;                      // Pin timeout policy

  SnapshotConfig()
  : name(""), description("") {}
};

/**
 * @brief Global system configuration
 */
struct GlobalConfig
{
  uint64_t max_total_buffer_memory;    // Maximum total memory for all buffers (MB)
  uint32_t recording_threads;          // Number of recording worker threads
  std::string log_level;               // Log level (DEBUG, INFO, WARN, ERROR)
  bool publish_stats;                  // Enable statistics publishing
  double stats_publish_rate;           // Statistics publish rate (Hz)

  // Time-addressed triggers
  std::string event_trigger_topic;     // EventTrigger subscription
  double max_trigger_latency;          // Extra buffer seconds for slow detectors (VLM lag)
  double write_settle_time;            // Wait past window end for in-flight messages (s)

  // Trigger budgets
  uint32_t max_clips_per_hour;         // Across all snapshots; 0 = unlimited
  double merge_gap;                    // Merge windows of one snapshot closer than this (s)
  double max_clip_duration;            // Never grow a merged clip past this (s)

  // Pins
  double pin_budget_fraction;          // Share of each topic buffer pins may hold past eviction

  // Written into every event.json
  std::string robot_id;
  std::map<std::string, std::string> metadata;

  GlobalConfig()
  : max_total_buffer_memory(2000),
    recording_threads(4),
    log_level("INFO"),
    publish_stats(true),
    stats_publish_rate(1.0),
    event_trigger_topic("/time_machine/event_trigger"),
    max_trigger_latency(0.0),
    write_settle_time(0.2),
    max_clips_per_hour(0),
    merge_gap(0.0),
    max_clip_duration(300.0),
    pin_budget_fraction(0.25) {}
};

/**
 * @brief Complete configuration for the time machine
 */
struct TimeMachineConfig
{
  std::vector<SnapshotConfig> snapshots;    // List of snapshot configurations
  GlobalConfig global;                       // Global settings

  TimeMachineConfig() {}
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__CONFIG_TYPES_HPP_
