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

#include <memory>
#include <string>
#include <thread>
#include <chrono>

#include "rclcpp/rclcpp.hpp"
#include "ros_time_machine/config_manager.hpp"
#include "ros_time_machine/buffer_manager.hpp"
#include "ros_time_machine/trigger_monitor.hpp"
#include "ros_time_machine/recorder.hpp"

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("time_machine_node");

  RCLCPP_INFO(node->get_logger(), "ROS Time Machine Node starting...");

  // Declare and get config file parameter
  node->declare_parameter<std::string>("config_file", "");
  std::string config_file;
  node->get_parameter("config_file", config_file);

  if (config_file.empty()) {
    RCLCPP_ERROR(node->get_logger(),
                 "No configuration file specified. Use --ros-args -p config_file:=<path>");
    return 1;
  }

  RCLCPP_INFO(node->get_logger(), "Using config file: %s", config_file.c_str());

  // Test config manager
  try {
    ros_time_machine::ConfigManager config_manager(node->get_logger());
    config_manager.loadFromFile(config_file);

    const auto & config = config_manager.getConfig();

    RCLCPP_INFO(node->get_logger(), "=== Configuration Summary ===");
    RCLCPP_INFO(node->get_logger(), "Snapshots configured: %zu", config.snapshots.size());

    for (const auto & snapshot : config.snapshots) {
      RCLCPP_INFO(node->get_logger(), "\nSnapshot: %s", snapshot.name.c_str());
      RCLCPP_INFO(node->get_logger(), "  Description: %s", snapshot.description.c_str());
      RCLCPP_INFO(node->get_logger(), "  Trigger topic: %s", snapshot.trigger.topic.c_str());
      RCLCPP_INFO(node->get_logger(), "  Message type: %s", snapshot.trigger.msg_type.c_str());
      RCLCPP_INFO(node->get_logger(), "  Recording topics: %zu", snapshot.recording.topics.size());
      for (const auto & topic : snapshot.recording.topics) {
        RCLCPP_INFO(node->get_logger(), "    - %s", topic.c_str());
      }
      RCLCPP_INFO(node->get_logger(), "  Pre-event: %.1fs, Post-event: %.1fs",
                  snapshot.recording.pre_event_duration,
                  snapshot.recording.post_event_duration);
      RCLCPP_INFO(node->get_logger(), "  Max buffer: %lu MB", snapshot.recording.max_buffer_size);
      RCLCPP_INFO(node->get_logger(), "  Rate limit: %u triggers/min",
                  snapshot.rate_limit.max_triggers_per_minute);
      RCLCPP_INFO(node->get_logger(), "  Output path: %s", snapshot.output.path.c_str());
    }

    RCLCPP_INFO(node->get_logger(), "\n=== Global Settings ===");
    RCLCPP_INFO(node->get_logger(), "Max total buffer memory: %lu MB",
                config.global.max_total_buffer_memory);
    RCLCPP_INFO(node->get_logger(), "Recording threads: %u", config.global.recording_threads);
    RCLCPP_INFO(node->get_logger(), "Log level: %s", config.global.log_level.c_str());
    RCLCPP_INFO(node->get_logger(), "Publish stats: %s",
                config.global.publish_stats ? "enabled" : "disabled");

    RCLCPP_INFO(node->get_logger(), "\n=== Configuration loaded successfully! ===");

    // Initialize Buffer Manager
    RCLCPP_INFO(node->get_logger(), "\n=== Initializing Buffer Manager ===");
    ros_time_machine::BufferManager buffer_manager(node, node->get_logger());

    if (!buffer_manager.initialize(config)) {
      RCLCPP_ERROR(node->get_logger(), "Failed to initialize buffer manager");
      return 1;
    }

    // Print buffered topics
    auto buffered_topics = buffer_manager.getBufferedTopics();
    RCLCPP_INFO(node->get_logger(), "Buffer Manager initialized for %zu topics:",
                buffered_topics.size());
    for (const auto & topic : buffered_topics) {
      RCLCPP_INFO(node->get_logger(), "  - %s", topic.c_str());
    }

    // Initialize Recorder
    RCLCPP_INFO(node->get_logger(), "\n=== Initializing Recorder ===");
    ros_time_machine::Recorder recorder(node, node->get_logger());

    if (!recorder.initialize(config, buffer_manager)) {
      RCLCPP_ERROR(node->get_logger(), "Failed to initialize recorder");
      return 1;
    }

    // Initialize Trigger Monitor
    RCLCPP_INFO(node->get_logger(), "\n=== Initializing Trigger Monitor ===");
    ros_time_machine::TriggerMonitor trigger_monitor(node, node->get_logger());

    // Define trigger callback
    auto trigger_callback = [&node, &recorder, &config](const ros_time_machine::TriggerEvent & event) {
      RCLCPP_INFO(node->get_logger(),
                  "*** TRIGGER EVENT *** Snapshot: %s, Topic: %s, Value: %.2f",
                  event.snapshot_name.c_str(),
                  event.trigger_topic.c_str(),
                  event.trigger_value);

      // Find the snapshot configuration
      for (const auto & snapshot : config.snapshots) {
        if (snapshot.name == event.snapshot_name) {
          recorder.handleTrigger(event, snapshot);
          break;
        }
      }
    };

    if (!trigger_monitor.initialize(config, trigger_callback)) {
      RCLCPP_ERROR(node->get_logger(), "Failed to initialize trigger monitor");
      return 1;
    }

    RCLCPP_INFO(node->get_logger(), "\n=== Time Machine Ready! ===");
    RCLCPP_INFO(node->get_logger(), "Buffering, trigger monitoring, and recording active.");

    // Spin to keep receiving messages
    RCLCPP_INFO(node->get_logger(), "Node running... (press Ctrl+C to exit)");
    rclcpp::spin(node);

  } catch (const ros_time_machine::ConfigException & e) {
    RCLCPP_ERROR(node->get_logger(), "Configuration error: %s", e.what());
    return 1;
  }

  rclcpp::shutdown();
  return 0;
}
