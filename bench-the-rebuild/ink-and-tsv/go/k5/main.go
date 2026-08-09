// K5 — TSV parsing, Go port. Same three kernels and the same canonical round-trip checksums as the
// Rust port and the Python original.
package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"strconv"
	"sync"
	"time"
)

type Row struct {
	Codepoints []uint32
	Glyphs     []string
	Clusters   []int64
	Seams      []string
	Positions  [][3]int64
}

type AuditRow struct {
	Config       string
	Codepoints   string
	Kinds        []string
	MatchedEntry string
	Baseline     []string
	New          []string
}

func parseHex(s string) uint32 {
	var v uint32
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c >= '0' && c <= '9':
			v = v*16 + uint32(c-'0')
		case c >= 'a' && c <= 'f':
			v = v*16 + uint32(c-'a'+10)
		default:
			v = v*16 + uint32(c-'A'+10)
		}
	}
	return v
}

func parseInt(s string) int64 {
	neg := false
	if len(s) > 0 && s[0] == '-' {
		neg = true
		s = s[1:]
	}
	var v int64
	for i := 0; i < len(s); i++ {
		v = v*10 + int64(s[i]-'0')
	}
	if neg {
		return -v
	}
	return v
}

func splitN(s string, sep byte, n int) []string {
	out := make([]string, 0, n)
	for {
		if len(out) == n-1 {
			out = append(out, s)
			return out
		}
		i := indexByte(s, sep)
		if i < 0 {
			out = append(out, s)
			return out
		}
		out = append(out, s[:i])
		s = s[i+1:]
	}
}

func indexByte(s string, c byte) int {
	for i := 0; i < len(s); i++ {
		if s[i] == c {
			return i
		}
	}
	return -1
}

func splitAll(s string, sep byte) []string {
	n := 1
	for i := 0; i < len(s); i++ {
		if s[i] == sep {
			n++
		}
	}
	out := make([]string, 0, n)
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == sep {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return append(out, s[start:])
}

func parseRows(text string) []Row {
	out := make([]Row, 0, 65536)
	for _, line := range splitAll(text, '\n') {
		if len(line) == 0 || line[0] == '#' {
			continue
		}
		f := splitN(line, '\t', 5)
		cps := splitAll(f[0], ':')
		codepoints := make([]uint32, len(cps))
		for i, c := range cps {
			codepoints[i] = parseHex(c)
		}
		cl := splitAll(f[2], ',')
		clusters := make([]int64, len(cl))
		for i, c := range cl {
			clusters[i] = parseInt(c)
		}
		var seams []string
		if f[3] != "" {
			seams = splitAll(f[3], ',')
		}
		ps := splitAll(f[4], '|')
		positions := make([][3]int64, len(ps))
		for i, p := range ps {
			t := splitN(p, ',', 3)
			positions[i] = [3]int64{parseInt(t[0]), parseInt(t[1]), parseInt(t[2])}
		}
		out = append(out, Row{codepoints, splitAll(f[1], '|'), clusters, seams, positions})
	}
	return out
}

func rowsChecksum(rows []Row) string {
	h := sha256.New()
	buf := make([]byte, 0, 4096)
	for _, r := range rows {
		buf = buf[:0]
		for i, cp := range r.Codepoints {
			if i > 0 {
				buf = append(buf, ':')
			}
			buf = append(buf, fmt.Sprintf("%04X", cp)...)
		}
		buf = append(buf, '\t')
		for i, g := range r.Glyphs {
			if i > 0 {
				buf = append(buf, '|')
			}
			buf = append(buf, g...)
		}
		buf = append(buf, '\t')
		for i, c := range r.Clusters {
			if i > 0 {
				buf = append(buf, ',')
			}
			buf = strconv.AppendInt(buf, c, 10)
		}
		buf = append(buf, '\t')
		for i, s := range r.Seams {
			if i > 0 {
				buf = append(buf, ',')
			}
			buf = append(buf, s...)
		}
		buf = append(buf, '\t')
		for i, p := range r.Positions {
			if i > 0 {
				buf = append(buf, '|')
			}
			buf = strconv.AppendInt(buf, p[0], 10)
			buf = append(buf, ',')
			buf = strconv.AppendInt(buf, p[1], 10)
			buf = append(buf, ',')
			buf = strconv.AppendInt(buf, p[2], 10)
		}
		buf = append(buf, '\n')
		h.Write(buf)
	}
	return fmt.Sprintf("%x", h.Sum(nil))
}

func parseAudit(text string) []AuditRow {
	lines := splitAll(text, '\n')
	out := make([]AuditRow, 0, 300000)
	for _, line := range lines[1:] {
		if len(line) == 0 {
			continue
		}
		f := splitN(line, '\t', 6)
		out = append(out, AuditRow{f[0], f[1], splitAll(f[2], ','), f[3], splitAll(f[4], '|'), splitAll(f[5], '|')})
	}
	return out
}

func auditChecksum(rows []AuditRow) string {
	h := sha256.New()
	buf := make([]byte, 0, 1024)
	for _, r := range rows {
		buf = buf[:0]
		buf = append(buf, r.Config...)
		buf = append(buf, '\t')
		buf = append(buf, r.Codepoints...)
		buf = append(buf, '\t')
		for i, k := range r.Kinds {
			if i > 0 {
				buf = append(buf, ',')
			}
			buf = append(buf, k...)
		}
		buf = append(buf, '\t')
		buf = append(buf, r.MatchedEntry...)
		buf = append(buf, '\t')
		for i, b := range r.Baseline {
			if i > 0 {
				buf = append(buf, '|')
			}
			buf = append(buf, b...)
		}
		buf = append(buf, '\t')
		for i, n := range r.New {
			if i > 0 {
				buf = append(buf, '|')
			}
			buf = append(buf, n...)
		}
		buf = append(buf, '\n')
		h.Write(buf)
	}
	return fmt.Sprintf("%x", h.Sum(nil))
}

var alphabet = map[uint32]bool{
	0x0020: true, 0x00B7: true, 0x200C: true, 0xE650: true, 0xE652: true, 0xE653: true,
	0xE658: true, 0xE65A: true, 0xE665: true, 0xE666: true, 0xE667: true, 0xE668: true,
	0xE670: true, 0xE672: true, 0xE675: true, 0xE676: true, 0xE679: true, 0xE67A: true,
}

func inAlphabet(tok []byte) bool {
	var v uint32
	for _, c := range tok {
		switch {
		case c >= '0' && c <= '9':
			v = v*16 + uint32(c-'0')
		case c >= 'a' && c <= 'f':
			v = v*16 + uint32(c-'a'+10)
		case c >= 'A' && c <= 'F':
			v = v*16 + uint32(c-'A'+10)
		default:
			return false
		}
	}
	return alphabet[v]
}

func keepLine(line []byte) bool {
	end := len(line)
	for i := 0; i < len(line); i++ {
		if line[i] == '\t' {
			end = i
			break
		}
	}
	field := line[:end]
	start := 0
	for i := 0; i <= len(field); i++ {
		if i == len(field) || field[i] == ':' {
			if !inAlphabet(field[start:i]) {
				return false
			}
			start = i + 1
		}
	}
	return true
}

func filterChunk(data []byte, out []byte) ([]byte, int) {
	kept := 0
	start := 0
	for i := 0; i <= len(data); i++ {
		if i == len(data) || data[i] == '\n' {
			line := data[start:i]
			start = i + 1
			if len(line) == 0 {
				continue
			}
			if line[0] == '#' {
				out = append(out, line...)
				out = append(out, '\n')
				continue
			}
			if keepLine(line) {
				out = append(out, line...)
				out = append(out, '\n')
				kept++
			}
		}
	}
	return out, kept
}

func splitAtLines(data []byte, parts int) [][]byte {
	bounds := []int{0}
	for i := 1; i < parts; i++ {
		p := len(data) * i / parts
		for p < len(data) && data[p] != '\n' {
			p++
		}
		if p < len(data) {
			p++
		}
		if p > len(data) {
			p = len(data)
		}
		if p > bounds[len(bounds)-1] {
			bounds = append(bounds, p)
		}
	}
	bounds = append(bounds, len(data))
	out := make([][]byte, 0, len(bounds)-1)
	for i := 0; i < len(bounds)-1; i++ {
		out = append(out, data[bounds[i]:bounds[i+1]])
	}
	return out
}

func main() {
	rowsPath, auditPath, bigPath, outDir := os.Args[1], os.Args[2], os.Args[3], os.Args[4]
	threads := 8
	if len(os.Args) > 5 {
		threads, _ = strconv.Atoi(os.Args[5])
	}

	rowsRaw, _ := os.ReadFile(rowsPath)
	rowsText := string(rowsRaw)
	bestRows := 1e30
	var rows []Row
	for i := 0; i < 5; i++ {
		t := time.Now()
		rows = parseRows(rowsText)
		if e := time.Since(t).Seconds(); e < bestRows {
			bestRows = e
		}
	}
	ckRows := rowsChecksum(rows)
	nRows := len(rows)
	rows = nil

	auditRaw, _ := os.ReadFile(auditPath)
	auditText := string(auditRaw)
	bestAudit := 1e30
	var audit []AuditRow
	for i := 0; i < 3; i++ {
		t := time.Now()
		audit = parseAudit(auditText)
		if e := time.Since(t).Seconds(); e < bestAudit {
			bestAudit = e
		}
	}
	ckAudit := auditChecksum(audit)
	nAudit := len(audit)
	audit = nil

	big, _ := os.ReadFile(bigPath)
	bestFilter := 1e30
	var out []byte
	kept := 0
	for i := 0; i < 2; i++ {
		t := time.Now()
		buf := make([]byte, 0, 8<<20)
		buf, kept = filterChunk(big, buf)
		if e := time.Since(t).Seconds(); e < bestFilter {
			bestFilter = e
		}
		out = buf
	}
	ckFilter := fmt.Sprintf("%x", sha256.Sum256(out))
	os.WriteFile(outDir+"/go.subset.tsv", out, 0o644)
	out = nil

	bestPar := 1e30
	keptPar := 0
	var ckPar string
	for i := 0; i < 2; i++ {
		t := time.Now()
		chunks := splitAtLines(big, threads)
		parts := make([][]byte, len(chunks))
		counts := make([]int, len(chunks))
		var wg sync.WaitGroup
		for w := range chunks {
			wg.Add(1)
			go func(w int) {
				defer wg.Done()
				buf := make([]byte, 0, 2<<20)
				buf, counts[w] = filterChunk(chunks[w], buf)
				parts[w] = buf
			}(w)
		}
		wg.Wait()
		merged := make([]byte, 0, 8<<20)
		total := 0
		for w := range parts {
			merged = append(merged, parts[w]...)
			total += counts[w]
		}
		if e := time.Since(t).Seconds(); e < bestPar {
			bestPar = e
		}
		keptPar = total
		ckPar = fmt.Sprintf("%x", sha256.Sum256(merged))
	}

	fmt.Printf("{\"rows_from_tsv\":{\"rows\":%d,\"go_seconds\":%.6f,\"go_ns_per_row\":%.1f,\"go_checksum\":\"%s\"},"+
		"\"load_audit\":{\"rows\":%d,\"go_seconds\":%.6f,\"go_ns_per_row\":%.1f,\"go_checksum\":\"%s\"},"+
		"\"filter_table\":{\"source_bytes\":%d,\"go_single_seconds\":%.6f,\"go_single_kept\":%d,\"go_single_checksum\":\"%s\","+
		"\"go_parallel_threads\":%d,\"go_parallel_seconds\":%.6f,\"go_parallel_kept\":%d,\"go_parallel_checksum\":\"%s\"}}\n",
		nRows, bestRows, bestRows/float64(nRows)*1e9, ckRows,
		nAudit, bestAudit, bestAudit/float64(nAudit)*1e9, ckAudit,
		len(big), bestFilter, kept, ckFilter, threads, bestPar, keptPar, ckPar)
}
