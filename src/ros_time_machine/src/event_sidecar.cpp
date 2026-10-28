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

#include "ros_time_machine/event_sidecar.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <sstream>

namespace ros_time_machine
{

namespace
{

std::string quoted(const std::string & value)
{
  return "\"" + jsonEscape(value) + "\"";
}

std::string number(double value)
{
  if (!std::isfinite(value)) {
    return "null";
  }
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.4g", value);
  return buf;
}

std::string stamp(int64_t ns)
{
  return quoted(formatIso8601(ns));
}

std::string stringArray(const std::vector<std::string> & values)
{
  std::string out = "[";
  for (size_t i = 0; i < values.size(); ++i) {
    out += (i ? ", " : "") + quoted(values[i]);
  }
  return out + "]";
}

void writeEvent(std::ostringstream & out, const EventRequest & event, const std::string & indent)
{
  out << indent << "\"label\": " << quoted(event.label) << ",\n";
  out << indent << "\"description\": " << quoted(event.description) << ",\n";
  out << indent << "\"confidence\": " << number(event.confidence) << ",\n";
  out << indent << "\"source\": " << quoted(event.source) << ",\n";
  out << indent << "\"source_version\": " << quoted(event.source_version) << ",\n";
  out << indent << "\"tags\": " << stringArray(event.tags) << ",\n";
  out << indent << "\"event_stamp\": " << stamp(event.event_ns) << ",\n";
  out << indent << "\"event_stamp_ns\": " << event.event_ns;
  if (event.received_ns != 0) {
    out << ",\n" << indent << "\"received_at\": " << stamp(event.received_ns) << ",\n";
    out << indent << "\"detection_lag_s\": " <<
      number((event.received_ns - event.event_ns) / 1e9);
  }
}

}  // namespace

std::string formatIso8601(int64_t ns)
{
  int64_t seconds = ns / 1000000000LL;
  int64_t millis = (ns % 1000000000LL) / 1000000LL;
  if (millis < 0) {
    seconds -= 1;
    millis += 1000;
  }
  std::time_t t = static_cast<std::time_t>(seconds);
  std::tm tm_utc{};
#ifdef _WIN32
  gmtime_s(&tm_utc, &t);
#else
  gmtime_r(&t, &tm_utc);
#endif
  char buf[40];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm_utc);
  char out[48];
  std::snprintf(out, sizeof(out), "%s.%03lldZ", buf, static_cast<long long>(millis));
  return out;
}

std::string jsonEscape(const std::string & value)
{
  std::string out;
  out.reserve(value.size());
  for (unsigned char c : value) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out += static_cast<char>(c);
        }
    }
  }
  return out;
}

std::string buildSidecarJson(const SidecarInfo & info)
{
  const PendingClip & clip = info.window;
  const EventRequest & primary = clip.primary();

  int64_t coverage_start = 0;
  int64_t coverage_end = 0;
  bool pre_event_complete = !info.topics.empty();
  bool first = true;
  for (const auto & topic : info.topics) {
    if (topic.message_count == 0) {
      continue;
    }
    coverage_start = first ? topic.first_ns : std::min(coverage_start, topic.first_ns);
    coverage_end = first ? topic.last_ns : std::max(coverage_end, topic.last_ns);
    first = false;
    pre_event_complete = pre_event_complete && topic.pre_event_complete;
  }

  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": " << quoted(kSidecarSchema) << ",\n";
  out << "  \"clip\": " << quoted(info.clip) << ",\n";
  out << "  \"profile\": " << quoted(clip.profile) << ",\n";
  out << "  \"storage\": " << quoted(info.storage) << ",\n";
  out << "  \"time_source\": " << quoted(info.time_source) << ",\n";
  out << "  \"event_stamp\": " << stamp(primary.event_ns) << ",\n";
  out << "  \"event_stamp_ns\": " << primary.event_ns << ",\n";
  out << "  \"window\": {\n";
  out << "    \"start\": " << stamp(clip.start_ns) << ",\n";
  out << "    \"end\": " << stamp(clip.end_ns) << ",\n";
  out << "    \"start_ns\": " << clip.start_ns << ",\n";
  out << "    \"end_ns\": " << clip.end_ns << "\n";
  out << "  },\n";
  // Every message comes from the full-rate hot tier until the warm tier lands
  out << "  \"full_rate_from\": " << (first ? "null" : stamp(coverage_start)) << ",\n";
  out << "  \"coverage\": {\n";
  out << "    \"start\": " << (first ? "null" : stamp(coverage_start)) << ",\n";
  out << "    \"end\": " << (first ? "null" : stamp(coverage_end)) << ",\n";
  out << "    \"pre_event_complete\": " << (pre_event_complete ? "true" : "false") << "\n";
  out << "  },\n";
  out << "  \"trigger\": {\n";
  writeEvent(out, primary, "    ");
  out << ",\n    \"merged_from\": " << clip.events.size() << "\n";
  out << "  },\n";
  out << "  \"events\": [\n";
  for (size_t i = 0; i < clip.events.size(); ++i) {
    out << "    {\n";
    writeEvent(out, clip.events[i], "      ");
    out << "\n    }" << (i + 1 < clip.events.size() ? "," : "") << "\n";
  }
  out << "  ],\n";
  out << "  \"robot\": {\n";
  out << "    \"id\": " << quoted(info.robot_id);
  for (const auto & [key, value] : info.robot_metadata) {
    out << ",\n    " << quoted(key) << ": " << quoted(value);
  }
  out << "\n  },\n";
  out << "  \"topics\": [\n";
  for (size_t i = 0; i < info.topics.size(); ++i) {
    const auto & topic = info.topics[i];
    out << "    {\"name\": " << quoted(topic.name) << ", \"type\": " << quoted(topic.type) <<
      ", \"messages\": " << topic.message_count <<
      ", \"first\": " << (topic.message_count ? stamp(topic.first_ns) : "null") <<
      ", \"last\": " << (topic.message_count ? stamp(topic.last_ns) : "null") <<
      ", \"pre_event_complete\": " << (topic.pre_event_complete ? "true" : "false") << "}" <<
      (i + 1 < info.topics.size() ? "," : "") << "\n";
  }
  out << "  ],\n";
  out << "  \"written_at\": " << stamp(info.written_at_ns) << ",\n";
  out << "  \"review\": null\n";
  out << "}\n";
  return out.str();
}

bool writeSidecar(const std::string & path, const SidecarInfo & info, std::string * error)
{
  try {
    std::string tmp_path = path + ".tmp";
    {
      std::ofstream file(tmp_path, std::ios::trunc);
      if (!file) {
        if (error) {*error = "cannot open " + tmp_path;}
        return false;
      }
      file << buildSidecarJson(info);
      if (!file) {
        if (error) {*error = "write failed for " + tmp_path;}
        return false;
      }
    }
    std::filesystem::rename(tmp_path, path);
    return true;
  } catch (const std::exception & e) {
    if (error) {*error = e.what();}
    return false;
  }
}

}  // namespace ros_time_machine
