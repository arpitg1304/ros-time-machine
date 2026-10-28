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

#include "ros_time_machine/config_manager.hpp"
#include <yaml-cpp/yaml.h>
#include <fstream>
#include <set>
#include <algorithm>

namespace ros_time_machine
{

// Helper function to convert string to ComparisonOperator
ComparisonOperator stringToOperator(const std::string & op_str)
{
  if (op_str == "==" || op_str == "=") {
    return ComparisonOperator::EQUAL;
  } else if (op_str == "!=") {
    return ComparisonOperator::NOT_EQUAL;
  } else if (op_str == ">") {
    return ComparisonOperator::GREATER;
  } else if (op_str == ">=") {
    return ComparisonOperator::GREATER_EQUAL;
  } else if (op_str == "<") {
    return ComparisonOperator::LESS;
  } else if (op_str == "<=") {
    return ComparisonOperator::LESS_EQUAL;
  } else {
    throw ConfigException("Invalid comparison operator: " + op_str);
  }
}

ConfigManager::ConfigManager(rclcpp::Logger logger)
  : logger_(logger), loaded_(false)
{
}

bool ConfigManager::loadFromFile(const std::string & config_file)
{
  RCLCPP_INFO(logger_, "Loading configuration from: %s", config_file.c_str());

  try {
    config_ = parseYaml(config_file);
    validate();
    loaded_ = true;
    RCLCPP_INFO(logger_, "Configuration loaded successfully with %zu snapshots",
                config_.snapshots.size());
    return true;
  } catch (const ConfigException & e) {
    RCLCPP_ERROR(logger_, "Configuration error: %s", e.what());
    loaded_ = false;
    throw;
  } catch (const YAML::Exception & e) {
    RCLCPP_ERROR(logger_, "YAML parsing error: %s", e.what());
    loaded_ = false;
    throw ConfigException("YAML parsing failed: " + std::string(e.what()));
  } catch (const std::exception & e) {
    RCLCPP_ERROR(logger_, "Unexpected error loading config: %s", e.what());
    loaded_ = false;
    throw ConfigException("Failed to load configuration: " + std::string(e.what()));
  }
}

const TimeMachineConfig & ConfigManager::getConfig() const
{
  if (!loaded_) {
    throw ConfigException("Configuration not loaded");
  }
  return config_;
}

const SnapshotConfig * ConfigManager::getSnapshot(const std::string & snapshot_name) const
{
  if (!loaded_) {
    return nullptr;
  }

  for (const auto & snapshot : config_.snapshots) {
    if (snapshot.name == snapshot_name) {
      return &snapshot;
    }
  }
  return nullptr;
}

std::vector<std::string> ConfigManager::getSnapshotNames() const
{
  std::vector<std::string> names;
  for (const auto & snapshot : config_.snapshots) {
    names.push_back(snapshot.name);
  }
  return names;
}

bool ConfigManager::isLoaded() const
{
  return loaded_;
}

TimeMachineConfig ConfigManager::parseYaml(const std::string & config_file)
{
  // Check if file exists
  std::ifstream file(config_file);
  if (!file.good()) {
    throw ConfigException("Configuration file not found: " + config_file);
  }

  YAML::Node root = YAML::LoadFile(config_file);
  TimeMachineConfig config;

  // Parse snapshots
  if (root["snapshots"]) {
    const YAML::Node & snapshots_node = root["snapshots"];
    if (!snapshots_node.IsSequence()) {
      throw ConfigException("'snapshots' must be a sequence");
    }

    for (const auto & snapshot_node : snapshots_node) {
      SnapshotConfig snapshot;

      // Parse name (required)
      if (!snapshot_node["name"]) {
        throw ConfigException("Snapshot missing required field 'name'");
      }
      snapshot.name = snapshot_node["name"].as<std::string>();

      // Parse description (optional)
      if (snapshot_node["description"]) {
        snapshot.description = snapshot_node["description"].as<std::string>();
      }

      // Parse trigger (optional: profiles without one fire only from EventTrigger,
      // pins and the TriggerSnapshot service)
      if (snapshot_node["trigger"]) {
        const YAML::Node & trigger_node = snapshot_node["trigger"];

        if (!trigger_node["topic"]) {
          throw ConfigException("Trigger missing required field 'topic'");
        }
        snapshot.trigger.topic = trigger_node["topic"].as<std::string>();

        if (!trigger_node["msg_type"]) {
          throw ConfigException("Trigger missing required field 'msg_type'");
        }
        snapshot.trigger.msg_type = trigger_node["msg_type"].as<std::string>();

        // Parse condition (required)
        if (!trigger_node["condition"]) {
          throw ConfigException("Trigger missing required field 'condition'");
        }
        const YAML::Node & cond_node = trigger_node["condition"];

        if (!cond_node["field"]) {
          throw ConfigException("Condition missing required field 'field'");
        }
        snapshot.trigger.condition.field = cond_node["field"].as<std::string>();

        if (!cond_node["operator"]) {
          throw ConfigException("Condition missing required field 'operator'");
        }
        std::string op_str = cond_node["operator"].as<std::string>();
        snapshot.trigger.condition.op = stringToOperator(op_str);

        if (!cond_node["value"]) {
          throw ConfigException("Condition missing required field 'value'");
        }
        // Support numeric, boolean, and string values
        const YAML::Node & value_node = cond_node["value"];
        try {
          // Try as boolean first (handles both quoted and unquoted true/false)
          try {
            bool bool_val = value_node.as<bool>();
            snapshot.trigger.condition.value = bool_val ? 1.0 : 0.0;
          } catch (const YAML::Exception &) {
            // Not a boolean, try as numeric
            snapshot.trigger.condition.value = value_node.as<double>();
          }
        } catch (const YAML::Exception & e) {
          throw ConfigException("Failed to parse condition value: " + std::string(e.what()));
        }
      }

      // Parse recording (required)
      if (!snapshot_node["recording"]) {
        throw ConfigException("Snapshot '" + snapshot.name + "' missing required field 'recording'");
      }
      const YAML::Node & rec_node = snapshot_node["recording"];

      if (!rec_node["topics"]) {
        throw ConfigException("Recording missing required field 'topics'");
      }
      if (!rec_node["topics"].IsSequence()) {
        throw ConfigException("Recording 'topics' must be a sequence");
      }
      for (const auto & topic_node : rec_node["topics"]) {
        snapshot.recording.topics.push_back(topic_node.as<std::string>());
      }

      if (!rec_node["pre_event_duration"]) {
        throw ConfigException("Recording missing required field 'pre_event_duration'");
      }
      snapshot.recording.pre_event_duration = rec_node["pre_event_duration"].as<double>();

      if (!rec_node["post_event_duration"]) {
        throw ConfigException("Recording missing required field 'post_event_duration'");
      }
      snapshot.recording.post_event_duration = rec_node["post_event_duration"].as<double>();

      if (rec_node["max_buffer_size"]) {
        snapshot.recording.max_buffer_size = rec_node["max_buffer_size"].as<uint64_t>();
      }

      // Parse rate_limit (optional)
      if (snapshot_node["rate_limit"]) {
        const YAML::Node & rate_node = snapshot_node["rate_limit"];
        if (rate_node["max_triggers_per_minute"]) {
          snapshot.rate_limit.max_triggers_per_minute =
            rate_node["max_triggers_per_minute"].as<uint32_t>();
        }
      }

      // Parse output (optional)
      if (snapshot_node["output"]) {
        const YAML::Node & output_node = snapshot_node["output"];
        if (output_node["path"]) {
          snapshot.output.path = output_node["path"].as<std::string>();
        }
        if (output_node["name_pattern"]) {
          snapshot.output.name_pattern = output_node["name_pattern"].as<std::string>();
        }
        if (output_node["format"]) {
          snapshot.output.format = output_node["format"].as<std::string>();
        }
      }

      // Parse pins (optional)
      if (snapshot_node["pins"]) {
        const YAML::Node & pins_node = snapshot_node["pins"];
        if (pins_node["timeout_policy"]) {
          std::string policy = pins_node["timeout_policy"].as<std::string>();
          if (policy == "keep") {
            snapshot.pins.keep_on_timeout = true;
          } else if (policy == "drop") {
            snapshot.pins.keep_on_timeout = false;
          } else {
            throw ConfigException(
              "Invalid pins.timeout_policy for snapshot '" + snapshot.name + "': " + policy +
              " (must be 'keep' or 'drop')");
          }
        }
        if (pins_node["default_ttl"]) {
          snapshot.pins.default_ttl = pins_node["default_ttl"].as<double>();
        }
      }

      config.snapshots.push_back(snapshot);
    }
  } else {
    throw ConfigException("Configuration missing required field 'snapshots'");
  }

  // Parse global settings (optional)
  if (root["global"]) {
    const YAML::Node & global_node = root["global"];

    if (global_node["max_total_buffer_memory"]) {
      config.global.max_total_buffer_memory =
        global_node["max_total_buffer_memory"].as<uint64_t>();
    }

    if (global_node["recording_threads"]) {
      config.global.recording_threads = global_node["recording_threads"].as<uint32_t>();
    }

    if (global_node["log_level"]) {
      config.global.log_level = global_node["log_level"].as<std::string>();
    }

    if (global_node["publish_stats"]) {
      config.global.publish_stats = global_node["publish_stats"].as<bool>();
    }

    if (global_node["stats_publish_rate"]) {
      config.global.stats_publish_rate = global_node["stats_publish_rate"].as<double>();
    }

    if (global_node["event_trigger_topic"]) {
      config.global.event_trigger_topic = global_node["event_trigger_topic"].as<std::string>();
    }

    if (global_node["max_trigger_latency"]) {
      config.global.max_trigger_latency = global_node["max_trigger_latency"].as<double>();
    }

    if (global_node["write_settle_time"]) {
      config.global.write_settle_time = global_node["write_settle_time"].as<double>();
    }

    if (global_node["pin_budget_fraction"]) {
      config.global.pin_budget_fraction = global_node["pin_budget_fraction"].as<double>();
    }

    if (global_node["budget"]) {
      const YAML::Node & budget_node = global_node["budget"];
      if (budget_node["max_clips_per_hour"]) {
        config.global.max_clips_per_hour = budget_node["max_clips_per_hour"].as<uint32_t>();
      }
      if (budget_node["merge_gap"]) {
        config.global.merge_gap = budget_node["merge_gap"].as<double>();
      }
      if (budget_node["max_clip_duration"]) {
        config.global.max_clip_duration = budget_node["max_clip_duration"].as<double>();
      }
    }

    if (global_node["robot"]) {
      const YAML::Node & robot_node = global_node["robot"];
      if (!robot_node.IsMap()) {
        throw ConfigException("'global.robot' must be a map");
      }
      for (const auto & entry : robot_node) {
        std::string key = entry.first.as<std::string>();
        std::string value = entry.second.as<std::string>();
        if (key == "id") {
          config.global.robot_id = value;
        } else {
          config.global.metadata[key] = value;
        }
      }
    }
  }

  return config;
}

bool ConfigManager::validate()
{
  RCLCPP_DEBUG(logger_, "Validating configuration...");

  // Check for at least one snapshot
  if (config_.snapshots.empty()) {
    throw ConfigException("Configuration must contain at least one snapshot");
  }

  // Check for unique snapshot names
  std::set<std::string> snapshot_names;
  for (const auto & snapshot : config_.snapshots) {
    if (snapshot_names.count(snapshot.name) > 0) {
      throw ConfigException("Duplicate snapshot name: " + snapshot.name);
    }
    snapshot_names.insert(snapshot.name);
  }

  // Validate each snapshot
  for (const auto & snapshot : config_.snapshots) {
    validateSnapshot(snapshot);
  }

  // Validate global config
  validateGlobal(config_.global);

  RCLCPP_DEBUG(logger_, "Configuration validation successful");
  return true;
}

void ConfigManager::validateSnapshot(const SnapshotConfig & snapshot)
{
  // Validate snapshot name
  if (snapshot.name.empty()) {
    throw ConfigException("Snapshot name cannot be empty");
  }

  if (snapshot.trigger.enabled()) {
    // Validate trigger topic
    if (!isValidTopicName(snapshot.trigger.topic)) {
      throw ConfigException("Invalid trigger topic name: " + snapshot.trigger.topic);
    }

    // Validate message type format (should contain at least one '/')
    if (snapshot.trigger.msg_type.find('/') == std::string::npos) {
      throw ConfigException(
        "Invalid message type format: " + snapshot.trigger.msg_type +
        " (expected format: package/msg/Type)");
    }

    // Validate condition field is not empty
    if (snapshot.trigger.condition.field.empty()) {
      throw ConfigException(
        "Trigger condition field cannot be empty for snapshot: " + snapshot.name);
    }
  }

  // Validate pin policy
  if (snapshot.pins.default_ttl <= 0.0) {
    throw ConfigException("pins.default_ttl must be > 0 for snapshot: " + snapshot.name);
  }

  // Validate recording topics
  if (snapshot.recording.topics.empty()) {
    throw ConfigException("Recording must specify at least one topic for snapshot: " + snapshot.name);
  }

  for (const auto & topic : snapshot.recording.topics) {
    if (!isValidTopicName(topic)) {
      throw ConfigException("Invalid recording topic name: " + topic);
    }
  }

  // Validate time durations
  if (snapshot.recording.pre_event_duration < 0.0) {
    throw ConfigException("pre_event_duration cannot be negative for snapshot: " + snapshot.name);
  }

  if (snapshot.recording.post_event_duration < 0.0) {
    throw ConfigException("post_event_duration cannot be negative for snapshot: " + snapshot.name);
  }

  if (snapshot.recording.pre_event_duration == 0.0 &&
      snapshot.recording.post_event_duration == 0.0)
  {
    throw ConfigException(
      "At least one of pre_event_duration or post_event_duration must be > 0 for snapshot: " +
      snapshot.name);
  }

  // Validate buffer size
  if (snapshot.recording.max_buffer_size == 0) {
    throw ConfigException("max_buffer_size must be > 0 for snapshot: " + snapshot.name);
  }

  // Validate rate limit
  if (snapshot.rate_limit.max_triggers_per_minute == 0) {
    throw ConfigException("max_triggers_per_minute must be > 0 for snapshot: " + snapshot.name);
  }

  // Validate output format
  if (snapshot.output.format != "mcap" && snapshot.output.format != "sqlite3") {
    throw ConfigException(
      "Invalid output format: " + snapshot.output.format +
      " (must be 'mcap' or 'sqlite3')");
  }

  RCLCPP_DEBUG(logger_, "Validated snapshot: %s", snapshot.name.c_str());
}

void ConfigManager::validateGlobal(const GlobalConfig & global)
{
  // Validate memory limit
  if (global.max_total_buffer_memory == 0) {
    throw ConfigException("max_total_buffer_memory must be > 0");
  }

  // Validate thread count
  if (global.recording_threads == 0) {
    throw ConfigException("recording_threads must be > 0");
  }

  // Validate log level
  std::set<std::string> valid_log_levels = {"DEBUG", "INFO", "WARN", "ERROR"};
  if (valid_log_levels.count(global.log_level) == 0) {
    throw ConfigException("Invalid log_level: " + global.log_level);
  }

  // Validate stats publish rate
  if (global.publish_stats && global.stats_publish_rate <= 0.0) {
    throw ConfigException("stats_publish_rate must be > 0 when publish_stats is enabled");
  }

  // Validate time-addressed trigger settings
  if (!isValidTopicName(global.event_trigger_topic)) {
    throw ConfigException("Invalid event_trigger_topic: " + global.event_trigger_topic);
  }

  if (global.max_trigger_latency < 0.0) {
    throw ConfigException("max_trigger_latency cannot be negative");
  }

  if (global.write_settle_time < 0.0) {
    throw ConfigException("write_settle_time cannot be negative");
  }

  if (global.pin_budget_fraction < 0.0 || global.pin_budget_fraction > 1.0) {
    throw ConfigException("pin_budget_fraction must be between 0 and 1");
  }

  if (global.merge_gap < 0.0) {
    throw ConfigException("budget.merge_gap cannot be negative");
  }

  if (global.max_clip_duration <= 0.0) {
    throw ConfigException("budget.max_clip_duration must be > 0");
  }

  RCLCPP_DEBUG(logger_, "Validated global configuration");
}

bool ConfigManager::isValidTopicName(const std::string & topic_name) const
{
  // Basic validation: topic name must not be empty
  if (topic_name.empty()) {
    return false;
  }

  // Topic names can start with '/' (standard ROS) or without (some legacy bags)
  // Just ensure it contains valid characters

  // Check for invalid characters (simple check)
  for (char c : topic_name) {
    if (!std::isalnum(c) && c != '/' && c != '_') {
      return false;
    }
  }

  return true;
}

double ConfigManager::getMaxPreEventDuration() const
{
  double max_duration = 0.0;
  for (const auto & snapshot : config_.snapshots) {
    max_duration = std::max(max_duration, snapshot.recording.pre_event_duration);
  }
  return max_duration;
}

}  // namespace ros_time_machine
