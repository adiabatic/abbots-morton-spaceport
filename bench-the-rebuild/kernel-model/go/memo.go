package main

// The memo sub-question in isolation: the same key stream as memo.py, on a 10-byte packed struct.

import (
	"fmt"
	"time"
	"unsafe"
)

const nKeys uint64 = 900000
const nLookups uint64 = 2428420

func mix(x uint64) uint64 {
	z := x + 0x9E3779B97F4A7C15
	z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
	z = (z ^ (z >> 27)) * 0x94D049BB133111EB
	return z ^ (z >> 31)
}

func memoKeyFor(i uint64) MemoKey {
	s := mix(i)
	return MemoKey{
		LeftKind: uint8(s % 5),
		LRune:    int8((s >> 3) % 18),
		LStance:  int8((s >> 8) % 4),
		LSeam:    int8((s>>12)%5) - 1,
		LExt:     int8((s >> 16) % 3),
		Token:    uint8((s >> 20) % 18),
		R1:       uint8((s >> 24) % 23),
		R2:       uint8((s >> 29) % 23),
		R3:       uint8((s >> 34) % 23),
		R4:       uint8((s >> 39) % 23),
	}
}

func runMemoBench() {
	t0 := time.Now()
	memo := make(map[MemoKey]uint64)
	for i := uint64(0); i < nKeys; i++ {
		memo[memoKeyFor(i)] = i
	}
	build := time.Since(t0).Seconds()
	t1 := time.Now()
	var checksum, hits uint64
	for j := uint64(0); j < nLookups; j++ {
		idx := mix(j^0xABCDEF) % (nKeys * 2)
		if v, ok := memo[memoKeyFor(idx)]; ok {
			checksum += v
			hits++
		}
	}
	lookup := time.Since(t1).Seconds()
	var k MemoKey
	fmt.Printf("{\"impl\":\"go\",\"n_keys\":%d,\"n_lookups\":%d,\"build_seconds\":%.6f,\"lookup_seconds\":%.6f,\"ns_per_lookup\":%.4f,\"hits\":%d,\"checksum\":%d,\"key_struct_bytes\":%d}\n",
		len(memo), nLookups, build, lookup, lookup*1e9/float64(nLookups), hits, checksum, unsafe.Sizeof(k))
}
