# Time-addressed trigger: "save the data around this moment"
# Published on /time_machine/event_trigger by rules, scorers, VLM watchers or humans.
# All times are robot ROS time (honors use_sim_time), never wall-clock "now".

# When the event happened. Zero = time the trigger was received.
builtin_interfaces/Time event_stamp

# Window to save. Zero = event_stamp -/+ the profile's pre/post_event_duration.
builtin_interfaces/Time start
builtin_interfaces/Time end

# Snapshot profile (topics, output) to use. "" = first configured snapshot.
string profile

# Short event type, e.g. "object_dropped"
string label

# Free-text caption from the detector
string description

# Detector confidence, 0..1
float32 confidence

# Who fired it: "rule:estop", "vlm:qwen3-vl-4b", "scorer:fiper", "human", "service"
string source

# Model + prompt hash, or rule id
string source_version

string[] tags
