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

#include <filesystem>
#include <fstream>
#include <sstream>

#include "ros_time_machine/event_sidecar.hpp"

using namespace ros_time_machine;

namespace
{
constexpr int64_t kSec = 1000000000LL;
// 2026-10-14T02:15:03.412Z
constexpr int64_t kEvent = 1791944103LL * kSec + 412000000LL;

SidecarInfo makeInfo()
{
  SidecarInfo info;
  info.clip = "object_dropped_1791944103_412000000";
  info.storage = "mcap";
  info.time_source = "ros";
  info.robot_id = "pip-01";
  info.robot_metadata["policy_version"] = "diffusion-v12";
  info.written_at_ns = kEvent + 4 * kSec;

  EventRequest event;
  event.profile = "manipulation";
  event.event_ns = kEvent;
  event.start_ns = kEvent - 5 * kSec;
  event.end_ns = kEvent + 3 * kSec;
  event.label = "object_dropped";
  event.description = "Blue box slips from the \"gripper\"\nand lands on the floor.";
  event.confidence = 0.82f;
  event.source = "vlm:qwen3-vl-4b-instruct";
  event.source_version = "prompt:3f9a1c";
  event.tags = {"demo"};
  event.received_ns = kEvent + 2500000000LL;

  info.window.id = 1;
  info.window.profile = "manipulation";
  info.window.start_ns = event.start_ns;
  info.window.end_ns = event.end_ns;
  info.window.events.push_back(event);

  TopicCoverage camera;
  camera.name = "/camera/front/image_raw/compressed";
  camera.type = "sensor_msgs/msg/CompressedImage";
  camera.message_count = 240;
  camera.first_ns = event.start_ns + 10000000;
  camera.last_ns = event.end_ns;
  info.topics.push_back(camera);
  return info;
}
}  // namespace

TEST(EventSidecarTest, FormatsIso8601Utc)
{
  EXPECT_EQ(formatIso8601(kEvent), "2026-10-14T02:15:03.412Z");
  EXPECT_EQ(formatIso8601(0), "1970-01-01T00:00:00.000Z");
  EXPECT_EQ(formatIso8601(1500000000LL), "1970-01-01T00:00:01.500Z");
}

TEST(EventSidecarTest, EscapesJsonStrings)
{
  EXPECT_EQ(jsonEscape("a\"b\\c\nd"), "a\\\"b\\\\c\\nd");
  EXPECT_EQ(jsonEscape(std::string("\x01", 1)), "\\u0001");
  EXPECT_EQ(jsonEscape("plain"), "plain");
}

TEST(EventSidecarTest, ContainsPlanSchemaFields)
{
  std::string json = buildSidecarJson(makeInfo());
  EXPECT_NE(json.find("\"schema\": \"rtm.event/1\""), std::string::npos);
  EXPECT_NE(json.find("\"event_stamp\": \"2026-10-14T02:15:03.412Z\""), std::string::npos);
  EXPECT_NE(json.find("\"start\": \"2026-10-14T02:14:58.412Z\""), std::string::npos);
  EXPECT_NE(json.find("\"end\": \"2026-10-14T02:15:06.412Z\""), std::string::npos);
  EXPECT_NE(json.find("\"source\": \"vlm:qwen3-vl-4b-instruct\""), std::string::npos);
  EXPECT_NE(json.find("\"confidence\": 0.82"), std::string::npos);
  EXPECT_NE(json.find("\"merged_from\": 1"), std::string::npos);
  EXPECT_NE(json.find("\"id\": \"pip-01\""), std::string::npos);
  EXPECT_NE(json.find("\"policy_version\": \"diffusion-v12\""), std::string::npos);
  EXPECT_NE(json.find("\"pre_event_complete\": true"), std::string::npos);
  EXPECT_NE(json.find("\"review\": null"), std::string::npos);
  EXPECT_NE(json.find("the \\\"gripper\\\"\\nand"), std::string::npos);
}

TEST(EventSidecarTest, ReportsDetectionLag)
{
  std::string json = buildSidecarJson(makeInfo());
  EXPECT_NE(json.find("\"detection_lag_s\": 2.5"), std::string::npos);
  EXPECT_NE(json.find("\"received_at\": \"2026-10-14T02:15:05.912Z\""), std::string::npos);

  SidecarInfo info = makeInfo();
  info.window.events[0].received_ns = 0;
  EXPECT_EQ(buildSidecarJson(info).find("detection_lag_s"), std::string::npos);
}

TEST(EventSidecarTest, IncompletePreEventIsReported)
{
  SidecarInfo info = makeInfo();
  info.topics[0].pre_event_complete = false;
  std::string json = buildSidecarJson(info);
  EXPECT_NE(json.find("\"pre_event_complete\": false"), std::string::npos);
}

TEST(EventSidecarTest, EmptyTopicsHaveNullCoverage)
{
  SidecarInfo info = makeInfo();
  info.topics[0].message_count = 0;
  std::string json = buildSidecarJson(info);
  EXPECT_NE(json.find("\"full_rate_from\": null"), std::string::npos);
  EXPECT_NE(json.find("\"first\": null"), std::string::npos);
}

TEST(EventSidecarTest, WritesFileAtomically)
{
  auto dir = std::filesystem::temp_directory_path() / "rtm_sidecar_test";
  std::filesystem::create_directories(dir);
  auto path = (dir / kSidecarFileName).string();

  std::string error;
  ASSERT_TRUE(writeSidecar(path, makeInfo(), &error)) << error;
  EXPECT_FALSE(std::filesystem::exists(path + ".tmp"));

  std::ifstream file(path);
  std::stringstream contents;
  contents << file.rdbuf();
  EXPECT_EQ(contents.str(), buildSidecarJson(makeInfo()));
  std::filesystem::remove_all(dir);
}

TEST(EventSidecarTest, ReportsWriteFailure)
{
  std::string error;
  EXPECT_FALSE(writeSidecar("/nonexistent_dir_rtm/event.json", makeInfo(), &error));
  EXPECT_FALSE(error.empty());
}

int main(int argc, char ** argv)
{
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
