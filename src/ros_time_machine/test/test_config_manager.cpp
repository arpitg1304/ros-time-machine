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

#include <gtest/gtest.h>
#include <fstream>
#include <filesystem>
#include "ros_time_machine/config_manager.hpp"
#include "ros_time_machine/config_types.hpp"
#include "rclcpp/rclcpp.hpp"

using namespace ros_time_machine;
namespace fs = std::filesystem;

// Test fixture for ConfigManager tests
class ConfigManagerTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    // Initialize ROS if not already initialized
    if (!rclcpp::ok()) {
      rclcpp::init(0, nullptr);
    }

    logger_ = std::make_shared<rclcpp::Logger>(rclcpp::get_logger("config_manager_test"));
    config_manager_ = std::make_unique<ConfigManager>(*logger_);

    // Get test config file paths
    test_dir_ = fs::path(__FILE__).parent_path();
    valid_config_path_ = test_dir_ / "test_config.yaml";
    invalid_config_path_ = test_dir_ / "test_config_invalid.yaml";
  }

  void TearDown() override
  {
    config_manager_.reset();
    logger_.reset();
  }

  std::shared_ptr<rclcpp::Logger> logger_;
  std::unique_ptr<ConfigManager> config_manager_;
  fs::path test_dir_;
  fs::path valid_config_path_;
  fs::path invalid_config_path_;
};

// Basic functionality tests
TEST_F(ConfigManagerTest, InitialStateNotLoaded)
{
  EXPECT_FALSE(config_manager_->isLoaded());
}

TEST_F(ConfigManagerTest, LoadValidConfiguration)
{
  bool result = config_manager_->loadFromFile(valid_config_path_.string());

  EXPECT_TRUE(result);
  EXPECT_TRUE(config_manager_->isLoaded());
}

TEST_F(ConfigManagerTest, LoadNonExistentFile)
{
  EXPECT_THROW(
    config_manager_->loadFromFile("/nonexistent/path/config.yaml"),
    ConfigException
  );
}

TEST_F(ConfigManagerTest, GetConfigBeforeLoad)
{
  // Should throw or return empty config
  EXPECT_THROW(
    config_manager_->getConfig(),
    std::exception
  );
}

// Configuration content tests
TEST_F(ConfigManagerTest, GetSnapshotNames)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  auto names = config_manager_->getSnapshotNames();

  ASSERT_EQ(names.size(), 1u);
  EXPECT_EQ(names[0], "test_snapshot");
}

TEST_F(ConfigManagerTest, GetSnapshotByName)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto * snapshot = config_manager_->getSnapshot("test_snapshot");

  ASSERT_NE(snapshot, nullptr);
  EXPECT_EQ(snapshot->name, "test_snapshot");
  EXPECT_EQ(snapshot->description, "Test snapshot configuration");
}

TEST_F(ConfigManagerTest, GetNonExistentSnapshot)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto * snapshot = config_manager_->getSnapshot("nonexistent");

  EXPECT_EQ(snapshot, nullptr);
}

// Validation tests
TEST_F(ConfigManagerTest, ValidateTriggerConfiguration)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto & config = config_manager_->getConfig();
  const auto & snapshot = config.snapshots[0];

  EXPECT_EQ(snapshot.trigger.topic, "/test/trigger");
  EXPECT_EQ(snapshot.trigger.msg_type, "std_msgs/msg/Bool");

  // TriggerCondition is a struct, not optional
  EXPECT_EQ(snapshot.trigger.condition.field, "data");
  EXPECT_EQ(snapshot.trigger.condition.op, ComparisonOperator::EQUAL);
  EXPECT_DOUBLE_EQ(snapshot.trigger.condition.value, 1.0);
}

TEST_F(ConfigManagerTest, ValidateRecordingConfiguration)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto & config = config_manager_->getConfig();
  const auto & snapshot = config.snapshots[0];

  EXPECT_EQ(snapshot.recording.topics.size(), 2u);
  EXPECT_EQ(snapshot.recording.topics[0], "/camera/image");
  EXPECT_EQ(snapshot.recording.topics[1], "/lidar/points");
  EXPECT_DOUBLE_EQ(snapshot.recording.pre_event_duration, 2.0);
  EXPECT_DOUBLE_EQ(snapshot.recording.post_event_duration, 3.0);
  EXPECT_EQ(snapshot.recording.max_buffer_size, 100u);
}

TEST_F(ConfigManagerTest, ValidateRateLimitConfiguration)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto & config = config_manager_->getConfig();
  const auto & snapshot = config.snapshots[0];

  EXPECT_EQ(snapshot.rate_limit.max_triggers_per_minute, 10u);
}

TEST_F(ConfigManagerTest, ValidateOutputConfiguration)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto & config = config_manager_->getConfig();
  const auto & snapshot = config.snapshots[0];

  EXPECT_EQ(snapshot.output.path, "/tmp/test_snapshots");
  EXPECT_EQ(snapshot.output.name_pattern, "{snapshot_name}_{timestamp}");
  EXPECT_EQ(snapshot.output.format, "mcap");
}

TEST_F(ConfigManagerTest, ValidateGlobalConfiguration)
{
  config_manager_->loadFromFile(valid_config_path_.string());

  const auto & config = config_manager_->getConfig();

  EXPECT_EQ(config.global.max_total_buffer_memory, 500u);
  EXPECT_EQ(config.global.recording_threads, 2u);
  EXPECT_EQ(config.global.log_level, "INFO");
  EXPECT_TRUE(config.global.publish_stats);
}

// Error handling tests
TEST_F(ConfigManagerTest, EmptyFilePath)
{
  EXPECT_THROW(
    config_manager_->loadFromFile(""),
    ConfigException
  );
}

// Reload configuration test
TEST_F(ConfigManagerTest, ReloadConfiguration)
{
  // Load first time
  bool result1 = config_manager_->loadFromFile(valid_config_path_.string());
  EXPECT_TRUE(result1);

  // Reload
  bool result2 = config_manager_->loadFromFile(valid_config_path_.string());
  EXPECT_TRUE(result2);
  EXPECT_TRUE(config_manager_->isLoaded());
}

// Multiple snapshots configuration test
TEST(ConfigManagerMultiSnapshotTest, MultipleSnapshots)
{
  // Create a temporary config with multiple snapshots
  const char * multi_snapshot_config = R"(
snapshots:
  - name: "snapshot1"
    description: "First snapshot"
    trigger:
      topic: "/trigger1"
      msg_type: "std_msgs/msg/Bool"
    recording:
      topics:
        - "/camera1"
      pre_event_duration: 1.0
      post_event_duration: 1.0
    rate_limit:
      max_triggers_per_minute: 5
    output:
      path: "/tmp/snap1"
      format: "mcap"

  - name: "snapshot2"
    description: "Second snapshot"
    trigger:
      topic: "/trigger2"
      msg_type: "std_msgs/msg/Bool"
    recording:
      topics:
        - "/camera2"
      pre_event_duration: 2.0
      post_event_duration: 2.0
    rate_limit:
      max_triggers_per_minute: 10
    output:
      path: "/tmp/snap2"
      format: "mcap"

global:
  max_total_buffer_memory: 1000
  recording_threads: 4
  log_level: "DEBUG"
  publish_stats: false
)";

  // Write to temporary file
  fs::path temp_config = fs::temp_directory_path() / "multi_snapshot_test.yaml";
  std::ofstream ofs(temp_config);
  ofs << multi_snapshot_config;
  ofs.close();

  // Test loading
  auto logger = rclcpp::get_logger("multi_snapshot_test");
  ConfigManager manager(logger);

  bool result = manager.loadFromFile(temp_config.string());
  EXPECT_TRUE(result);

  auto names = manager.getSnapshotNames();
  ASSERT_EQ(names.size(), 2u);

  // Check both snapshots are accessible
  const auto * snap1 = manager.getSnapshot("snapshot1");
  const auto * snap2 = manager.getSnapshot("snapshot2");

  ASSERT_NE(snap1, nullptr);
  ASSERT_NE(snap2, nullptr);

  EXPECT_EQ(snap1->trigger.topic, "/trigger1");
  EXPECT_EQ(snap2->trigger.topic, "/trigger2");

  // Cleanup
  fs::remove(temp_config);
}

// Edge cases and validation tests
TEST(ConfigManagerEdgeCasesTest, EmptyTopicsList)
{
  const char * empty_topics_config = R"(
snapshots:
  - name: "empty_topics"
    description: "No recording topics"
    trigger:
      topic: "/trigger"
      msg_type: "std_msgs/msg/Bool"
    recording:
      topics: []
      pre_event_duration: 1.0
      post_event_duration: 1.0
    rate_limit:
      max_triggers_per_minute: 5
    output:
      path: "/tmp/test"
      format: "mcap"

global:
  max_total_buffer_memory: 500
  recording_threads: 2
  log_level: "INFO"
  publish_stats: true
)";

  fs::path temp_config = fs::temp_directory_path() / "empty_topics_test.yaml";
  std::ofstream ofs(temp_config);
  ofs << empty_topics_config;
  ofs.close();

  auto logger = rclcpp::get_logger("empty_topics_test");
  ConfigManager manager(logger);

  // Should throw validation error for empty topics list
  EXPECT_THROW(
    manager.loadFromFile(temp_config.string()),
    ConfigException
  );

  fs::remove(temp_config);
}

TEST(ConfigManagerEdgeCasesTest, NegativeDurations)
{
  const char * negative_duration_config = R"(
snapshots:
  - name: "negative_duration"
    description: "Negative durations"
    trigger:
      topic: "/trigger"
      msg_type: "std_msgs/msg/Bool"
    recording:
      topics:
        - "/test"
      pre_event_duration: -1.0
      post_event_duration: 1.0
    rate_limit:
      max_triggers_per_minute: 5
    output:
      path: "/tmp/test"
      format: "mcap"

global:
  max_total_buffer_memory: 500
  recording_threads: 2
  log_level: "INFO"
  publish_stats: true
)";

  fs::path temp_config = fs::temp_directory_path() / "negative_duration_test.yaml";
  std::ofstream ofs(temp_config);
  ofs << negative_duration_config;
  ofs.close();

  auto logger = rclcpp::get_logger("negative_duration_test");
  ConfigManager manager(logger);

  // Should throw validation error for negative duration
  EXPECT_THROW(
    manager.loadFromFile(temp_config.string()),
    ConfigException
  );

  fs::remove(temp_config);
}

// v2: time-addressed triggers, pins, budgets
namespace
{
// Writes yaml to a temp file and loads it; returns the manager for inspection
std::unique_ptr<ConfigManager> loadYaml(const std::string & name, const std::string & yaml)
{
  fs::path temp_config = fs::temp_directory_path() / (name + ".yaml");
  {
    std::ofstream ofs(temp_config);
    ofs << yaml;
  }
  auto manager = std::make_unique<ConfigManager>(rclcpp::get_logger(name));
  try {
    manager->loadFromFile(temp_config.string());
  } catch (...) {
    fs::remove(temp_config);
    throw;
  }
  fs::remove(temp_config);
  return manager;
}

const char * kV2Config = R"(
snapshots:
  - name: "manipulation"
    description: "VLM-triggered manipulation events"
    recording:
      topics: ["/camera/front/image_raw/compressed", "/joint_states"]
      pre_event_duration: 5.0
      post_event_duration: 3.0
    pins:
      timeout_policy: "drop"
  - name: "safety"
    trigger:
      topic: "/estop"
      msg_type: "std_msgs/msg/Bool"
      condition: {field: "data", operator: "==", value: true}
    recording:
      topics: ["/joint_states"]
      pre_event_duration: 5.0
      post_event_duration: 5.0
    pins:
      timeout_policy: "keep"
      default_ttl: 12.5
    output:
      name_pattern: "{label}_{timestamp}"

global:
  event_trigger_topic: "/rtm/events"
  max_trigger_latency: 7.0
  write_settle_time: 0.5
  pin_budget_fraction: 0.4
  budget:
    max_clips_per_hour: 60
    merge_gap: 1.5
    max_clip_duration: 90
  robot:
    id: "pip-01"
    policy_version: "diffusion-v12"
)";
}  // namespace

TEST(ConfigManagerV2Test, SnapshotWithoutTriggerIsAProfile)
{
  auto manager = loadYaml("v2_profiles", kV2Config);
  const auto * manipulation = manager->getSnapshot("manipulation");
  ASSERT_NE(manipulation, nullptr);
  EXPECT_FALSE(manipulation->trigger.enabled());
  const auto * safety = manager->getSnapshot("safety");
  ASSERT_NE(safety, nullptr);
  EXPECT_TRUE(safety->trigger.enabled());
}

TEST(ConfigManagerV2Test, PinPolicyParsed)
{
  auto manager = loadYaml("v2_pins", kV2Config);
  const auto * manipulation = manager->getSnapshot("manipulation");
  const auto * safety = manager->getSnapshot("safety");
  EXPECT_FALSE(manipulation->pins.keep_on_timeout);
  EXPECT_DOUBLE_EQ(manipulation->pins.default_ttl, 30.0);
  EXPECT_TRUE(safety->pins.keep_on_timeout);
  EXPECT_DOUBLE_EQ(safety->pins.default_ttl, 12.5);
  EXPECT_EQ(safety->output.name_pattern, "{label}_{timestamp}");
}

TEST(ConfigManagerV2Test, GlobalTriggerSettingsParsed)
{
  auto manager = loadYaml("v2_global", kV2Config);
  const auto & global = manager->getConfig().global;
  EXPECT_EQ(global.event_trigger_topic, "/rtm/events");
  EXPECT_DOUBLE_EQ(global.max_trigger_latency, 7.0);
  EXPECT_DOUBLE_EQ(global.write_settle_time, 0.5);
  EXPECT_DOUBLE_EQ(global.pin_budget_fraction, 0.4);
  EXPECT_EQ(global.max_clips_per_hour, 60u);
  EXPECT_DOUBLE_EQ(global.merge_gap, 1.5);
  EXPECT_DOUBLE_EQ(global.max_clip_duration, 90.0);
  EXPECT_EQ(global.robot_id, "pip-01");
  ASSERT_EQ(global.metadata.count("policy_version"), 1u);
  EXPECT_EQ(global.metadata.at("policy_version"), "diffusion-v12");
}

TEST(ConfigManagerV2Test, V1ConfigGetsV2Defaults)
{
  ConfigManager manager(rclcpp::get_logger("v2_defaults"));
  fs::path config = fs::path(__FILE__).parent_path() / "test_config.yaml";
  manager.loadFromFile(config.string());
  const auto & global = manager.getConfig().global;
  EXPECT_EQ(global.event_trigger_topic, "/time_machine/event_trigger");
  EXPECT_DOUBLE_EQ(global.max_trigger_latency, 0.0);
  EXPECT_EQ(global.max_clips_per_hour, 0u);
  EXPECT_TRUE(manager.getConfig().snapshots.front().trigger.enabled());
}

TEST(ConfigManagerV2Test, InvalidPinPolicyRejected)
{
  std::string yaml = kV2Config;
  yaml.replace(yaml.find("\"keep\""), 6, "\"maybe\"");
  EXPECT_THROW(loadYaml("v2_bad_policy", yaml), ConfigException);
}

TEST(ConfigManagerV2Test, InvalidBudgetsRejected)
{
  std::string fraction = kV2Config;
  fraction.replace(fraction.find("0.4"), 3, "1.5");
  EXPECT_THROW(loadYaml("v2_bad_fraction", fraction), ConfigException);

  std::string latency = kV2Config;
  latency.replace(latency.find("7.0"), 3, "-1");
  EXPECT_THROW(loadYaml("v2_bad_latency", latency), ConfigException);

  std::string duration = kV2Config;
  duration.replace(duration.find("max_clip_duration: 90"), 21, "max_clip_duration: 0");
  EXPECT_THROW(loadYaml("v2_bad_duration", duration), ConfigException);
}

TEST(ConfigManagerV2Test, PartialTriggerStillValidated)
{
  // A trigger block that is present must still be complete
  std::string yaml = kV2Config;
  yaml.replace(yaml.find("      msg_type: \"std_msgs/msg/Bool\"\n"), 36, "");
  EXPECT_THROW(loadYaml("v2_partial_trigger", yaml), ConfigException);
}

int main(int argc, char ** argv)
{
  testing::InitGoogleTest(&argc, argv);
  rclcpp::init(argc, argv);
  int result = RUN_ALL_TESTS();
  rclcpp::shutdown();
  return result;
}
