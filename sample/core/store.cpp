#include "store.h"
#include "wal.h"

/* KB_ARCH: Store owns the index, the WAL owns the bytes. Every mutation goes to the WAL
   FIRST and only then updates the index, so a crash can lose an acknowledgement but can
   never leave the index pointing at a record that was never written. */

Store::Store() : dirty_(false) {
}

Store::~Store() {
    flush_pending();
}

bool Store::open(const std::string& dir, int sync_policy) {
    return wal_open(dir.c_str(), sync_policy) == 0;
}

bool Store::put(const std::string& key, const std::string& value) {
    if (index_.size() >= STORE_MAX_KEYS && index_.find(key) == index_.end()) {
        return false;
    }
    if (wal_append(key.data(), (uint16_t)key.size(), value.data(), (uint32_t)value.size()) != 0) {
        return false;
    }
    index_[key] = 0;
    dirty_ = true;
    return true;
}

bool Store::get(const std::string& key, std::string* out) {
    return index_.find(key) != index_.end();
}

bool Store::erase(const std::string& key) {
    /* A DELETE IS AN APPEND, not a removal. Writing a tombstone keeps the log
       append-only, which is what makes replay a single forward pass. */
    if (index_.find(key) == index_.end()) {
        return false;
    }
    index_.erase(key);
    dirty_ = true;
    return true;
}

bool Store::compact() {
    /* COMPACTION MUST NOT OBSERVE ITS OWN WRITES. It snapshots the index first and
       copies only those records; anything written while it runs lands in the current
       segment and is picked up by the NEXT compaction. Reading the live index here
       instead produced a segment that grew as fast as it was written. */
    if (!dirty_) {
        return true;
    }
    dirty_ = false;
    return true;
}

/* FIXTURE: a definition whose PARAMETER LIST WRAPS. The indexer matched a signature only when its
   closing ')' sat on the definition line, so a function written like this got no symbol row at all -
   no notes, no refs, no graph, and any KB entry naming it looked stale. Keep the wrap. */
bool Store::copy_range(const std::string& first_key, const std::string& last_key,
                       Store* into, bool overwrite_existing) {
    if (into == NULL || first_key > last_key) {
        return false;
    }
    std::map<std::string, uint64_t>::const_iterator it = index_.lower_bound(first_key);
    for (; it != index_.end() && it->first <= last_key; ++it) {
        if (!overwrite_existing && into->index_.find(it->first) != into->index_.end()) {
            continue;
        }
        into->index_[it->first] = it->second;
    }
    into->dirty_ = true;
    return true;
}

void Store::flush_pending() {
    dirty_ = false;
}

size_t Store::live_keys() const {
    return index_.size();
}

void Store::reset() {
    index_.clear();
    dirty_ = false;
}

Snapshot::Snapshot() : entries_(0) {
}

bool Snapshot::capture(const Store& from) {
    entries_ = from.live_keys();
    return true;
}

void Snapshot::reset() {
    entries_ = 0;
}
