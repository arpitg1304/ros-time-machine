# Buffer statistics message
# Published periodically to report buffer health and usage

# Topic name
string topic_name

# Current buffer size (number of messages)
uint64 message_count

# Current buffer memory usage (bytes)
uint64 memory_bytes

# Configured maximum buffer size (bytes)
uint64 max_buffer_bytes

# Buffer utilization percentage (0-100)
float32 utilization_percent

# Number of messages dropped due to buffer overflow
uint64 messages_dropped

# Timestamp of oldest message in buffer
builtin_interfaces/Time oldest_message_time

# Timestamp of newest message in buffer
builtin_interfaces/Time newest_message_time

# Messages held past normal eviction because they fall inside a pinned range
uint64 pinned_message_count

# Number of pins currently awaiting a keep/release decision (same on every topic)
uint32 active_pins
