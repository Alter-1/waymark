#ifndef KVLOG_STORE_H
#define KVLOG_STORE_H

#include <string>
#include <map>

#define STORE_MAX_KEYS     100000
#define STORE_COMPACT_RATIO 2

/* The in-memory index in front of the WAL. Keys live here; values live on disk and are
   read back by offset. */
class Store {
public:
    Store();
    ~Store();

    bool open(const std::string& dir, int sync_policy);
    bool put(const std::string& key, const std::string& value);
    bool get(const std::string& key, std::string* out);
    bool erase(const std::string& key);

    /* Rewrite the log keeping only live records. Safe to call while readers are active. */
    bool compact();

    /* Flush whatever the WAL is holding. Called by the idle timer and by close(). */
    void flush_pending();

    size_t live_keys() const;

private:
    std::map<std::string, uint64_t> index_;
    bool dirty_;
};

#endif
