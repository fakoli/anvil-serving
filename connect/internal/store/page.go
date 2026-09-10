package store

import (
	"bytes"
	"encoding/json"
)

// Page returns copied records after one exact key in an ordered namespace.
// Both allocation and work are bounded independently of total bucket size.
func (t *Tx) Page(bucket, prefix, after string, limit int) ([]Record, bool, error) {
	if len(prefix) > 256 || len(after) > 256 || limit < 1 || limit > 256 || (after != "" && !bytes.HasPrefix([]byte(after), []byte(prefix))) {
		return nil, false, ErrState
	}
	b, err := t.bucket(bucket)
	if err != nil {
		return nil, false, err
	}
	cursor := b.Cursor()
	seek := prefix
	if after != "" {
		seek = after
	}
	key, value := cursor.Seek([]byte(seek))
	if string(key) == after && after != "" {
		key, value = cursor.Next()
	}
	result := []Record{}
	size := 0
	for key != nil && bytes.HasPrefix(key, []byte(prefix)) {
		if len(result) == limit {
			return result, true, nil
		}
		size += len(key) + len(value)
		if len(key) < 1 || len(key) > 256 || value == nil || len(value) > 65536 || size > 2*1024*1024 || !json.Valid(value) {
			return nil, false, ErrState
		}
		result = append(result, Record{ID: string(key), Value: append(json.RawMessage(nil), value...)})
		key, value = cursor.Next()
	}
	return result, false, nil
}
