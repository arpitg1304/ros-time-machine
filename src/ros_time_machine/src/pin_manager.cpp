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

#include "ros_time_machine/pin_manager.hpp"

#include <algorithm>

namespace ros_time_machine
{

uint64_t PinManager::add(
  int64_t start_ns, int64_t end_ns, double ttl_seconds, int64_t now_ns,
  const std::string & profile, const std::string & reason)
{
  std::lock_guard<std::mutex> lock(mutex_);

  if (start_ns > end_ns) {
    std::swap(start_ns, end_ns);
  }
  double ttl = ttl_seconds > 0.0 ? ttl_seconds : default_ttl_seconds_;

  Pin pin;
  pin.id = next_id_++;
  pin.start_ns = start_ns;
  pin.end_ns = end_ns;
  pin.expires_ns = now_ns + static_cast<int64_t>(ttl * 1e9);
  pin.profile = profile;
  pin.reason = reason;
  pins_[pin.id] = pin;
  return pin.id;
}

std::optional<Pin> PinManager::get(uint64_t pin_id) const
{
  std::lock_guard<std::mutex> lock(mutex_);
  auto it = pins_.find(pin_id);
  if (it == pins_.end()) {
    return std::nullopt;
  }
  return it->second;
}

std::optional<Pin> PinManager::take(uint64_t pin_id)
{
  std::lock_guard<std::mutex> lock(mutex_);
  auto it = pins_.find(pin_id);
  if (it == pins_.end()) {
    return std::nullopt;
  }
  Pin pin = it->second;
  pins_.erase(it);
  return pin;
}

std::vector<Pin> PinManager::takeExpired(int64_t now_ns)
{
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<Pin> expired;
  for (auto it = pins_.begin(); it != pins_.end(); ) {
    if (it->second.expires_ns <= now_ns) {
      expired.push_back(it->second);
      it = pins_.erase(it);
    } else {
      ++it;
    }
  }
  return expired;
}

std::vector<Pin> PinManager::expired(int64_t now_ns) const
{
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<Pin> result;
  for (const auto & [_, pin] : pins_) {
    if (pin.expires_ns <= now_ns) {
      result.push_back(pin);
    }
  }
  return result;
}

bool PinManager::covers(int64_t t_ns) const
{
  std::lock_guard<std::mutex> lock(mutex_);
  for (const auto & [_, pin] : pins_) {
    if (t_ns >= pin.start_ns && t_ns <= pin.end_ns) {
      return true;
    }
  }
  return false;
}

size_t PinManager::activeCount() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return pins_.size();
}

std::vector<Pin> PinManager::list() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<Pin> result;
  result.reserve(pins_.size());
  for (const auto & [_, pin] : pins_) {
    result.push_back(pin);
  }
  return result;
}

void PinManager::setDefaultTtl(double seconds)
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (seconds > 0.0) {
    default_ttl_seconds_ = seconds;
  }
}

}  // namespace ros_time_machine
