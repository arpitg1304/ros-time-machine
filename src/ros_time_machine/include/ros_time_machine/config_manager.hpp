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

#ifndef ROS_TIME_MACHINE__CONFIG_MANAGER_HPP_
#define ROS_TIME_MACHINE__CONFIG_MANAGER_HPP_

#include <string>
#include <memory>
#include <stdexcept>

#include "ros_time_machine/config_types.hpp"
#include "rclcpp/rclcpp.hpp"

namespace ros_time_machine
{

/**
 * @brief Exception thrown when configuration is invalid
 */
class ConfigException : public std::runtime_error
{
public:
  explicit ConfigException(const std::string & message)
    : std::runtime_error(message) {}
};

/**
 * @brief Manages configuration parsing and validation
 *
 * The ConfigManager is responsible for:
 * - Loading configuration from YAML files
 * - Validating configuration parameters
 * - Providing access to configuration data
 * - Hot-reloading configuration (future feature)
 */
class ConfigManager
{
public:
  /**
   * @brief Construct a new Config Manager
   *
   * @param logger ROS logger for diagnostic messages
   */
  explicit ConfigManager(rclcpp::Logger logger);

  /**
   * @brief Load configuration from a YAML file
   *
   * @param config_file Path to YAML configuration file
   * @return true if configuration loaded successfully
   * @return false if configuration failed to load
   * @throws ConfigException if configuration is invalid
   */
  bool loadFromFile(const std::string & config_file);

  /**
   * @brief Get the complete configuration
   *
   * @return const TimeMachineConfig& Reference to configuration
   */
  const TimeMachineConfig & getConfig() const;

  /**
   * @brief Get a specific snapshot configuration by name
   *
   * @param snapshot_name Name of the snapshot
   * @return const SnapshotConfig* Pointer to snapshot config, or nullptr if not found
   */
  const SnapshotConfig * getSnapshot(const std::string & snapshot_name) const;

  /**
   * @brief Get all snapshot names
   *
   * @return std::vector<std::string> List of snapshot names
   */
  std::vector<std::string> getSnapshotNames() const;

  /**
   * @brief Check if configuration is loaded
   *
   * @return true if configuration is loaded and valid
   */
  bool isLoaded() const;

  /**
   * @brief Validate the current configuration
   *
   * Performs comprehensive validation including:
   * - Unique snapshot names
   * - Valid topic names
   * - Valid message types
   * - Reasonable parameter values
   *
   * @return true if configuration is valid
   * @throws ConfigException if configuration is invalid
   */
  bool validate();

private:
  /**
   * @brief Parse YAML node into TimeMachineConfig
   *
   * @param config_file Path to YAML file
   * @return TimeMachineConfig Parsed configuration
   */
  TimeMachineConfig parseYaml(const std::string & config_file);

  /**
   * @brief Validate a single snapshot configuration
   *
   * @param snapshot Snapshot configuration to validate
   * @throws ConfigException if snapshot is invalid
   */
  void validateSnapshot(const SnapshotConfig & snapshot);

  /**
   * @brief Validate global configuration
   *
   * @param global Global configuration to validate
   * @throws ConfigException if global config is invalid
   */
  void validateGlobal(const GlobalConfig & global);

  /**
   * @brief Check if a topic name is valid
   *
   * @param topic_name Topic name to check
   * @return true if valid
   */
  bool isValidTopicName(const std::string & topic_name) const;

  /**
   * @brief Calculate maximum pre-event duration across all snapshots
   *
   * @return double Maximum pre-event duration in seconds
   */
  double getMaxPreEventDuration() const;

  rclcpp::Logger logger_;
  TimeMachineConfig config_;
  bool loaded_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__CONFIG_MANAGER_HPP_
