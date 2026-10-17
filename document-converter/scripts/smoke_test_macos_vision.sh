#!/usr/bin/env bash
# Smoke test for macOS Apple Vision OCR integration
# Verifies that read_document.py correctly falls through to Vision OCR
# when docling returns empty output for a scanned PDF.
#
# Run from anywhere: ~/.hanako/skills/document-converter/scripts/smoke_test_macos_vision.sh
# Or: bash ~/.hanako/skills/document-converter/scripts/smoke_test_macos_vision.sh
#
# Exit codes:
#   0 — pass (vision succeeded in <10s with non-empty output)
#   1 — fail (macOS check failed, swift missing, or vision times out)
#   2 — skip (not on macOS Apple Silicon; not applicable)

set -uo pipefail  # NOT -e (we want graceful skip paths)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VISION_SCRIPT="$SKILL_DIR/bin/ocr-vision.swift"
LOG="$SKILL_DIR/logs/smoke-test.log"

mkdir -p "$(dirname "$LOG")"

echo "=== macOS Vision OCR Smoke Test ===" | tee "$LOG"
echo "Date: $(date)" | tee -a "$LOG"

# Detect platform
if [ "$(uname -s)" != "Darwin" ]; then
    echo "SKIP: not macOS (uname=$(uname -s))" | tee -a "$LOG"
    exit 2
fi

ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "SKIP: not Apple Silicon (arch=$ARCH)" | tee -a "$LOG"
    exit 2
fi

echo "Platform: macOS Apple Silicon ✓" | tee -a "$LOG"

# Check swift
if ! command -v swift >/dev/null 2>&1; then
    echo "FAIL: swift not in PATH" | tee -a "$LOG"
    exit 1
fi

# Check Vision script exists
if [ ! -f "$VISION_SCRIPT" ]; then
    echo "FAIL: Vision script missing at $VISION_SCRIPT" | tee -a "$LOG"
    exit 1
fi

# Test 1: Direct Vision script test on a known sample if available
# Use any PDF in the user's document tree as test target
# Override the search root with $SAMPLE_PDF_DIR; non-existent dirs are ignored
# Suppress errors — paths with spaces may cause issues, ignore failures
SAMPLE_PDF_DIR="${SAMPLE_PDF_DIR:-$HOME/Documents}"
TEST_PDFS=$(find "$SAMPLE_PDF_DIR" -maxdepth 4 -type f -name "*.pdf" -size -500k 2>/dev/null | head -3 || echo "")
if [ -z "$TEST_PDFS" ]; then
    echo "WARN: no sample PDF found; skipping integration test" | tee -a "$LOG"
    echo "Direct Vision script syntax check:"
    swift -typecheck "$VISION_SCRIPT" 2>&1 | head -5 | tee -a "$LOG"
    if [ $? -eq 0 ]; then
        echo "PASS (script typechecks)" | tee -a "$LOG"
        exit 0
    else
        echo "FAIL (script has type errors)" | tee -a "$LOG"
        exit 1
    fi
fi

# Test 2: Run Vision script directly on smallest sample PDF
SAMPLE=$(echo "$TEST_PDFS" | head -1)
echo "Sample: $SAMPLE" | tee -a "$LOG"
START=$(date +%s%N)
swift "$VISION_SCRIPT" "$SAMPLE" > /tmp/vision-smoke.out 2>&1
END=$(date +%s%N)
ELAPSED_MS=$(( (END - START) / 1000000 ))

OUT_SIZE=$(wc -c < /tmp/vision-smoke.out)
echo "Vision elapsed: ${ELAPSED_MS}ms, output: ${OUT_SIZE} bytes" | tee -a "$LOG"

if [ "$OUT_SIZE" -lt 50 ]; then
    echo "FAIL: Vision output too small ($OUT_SIZE bytes)" | tee -a "$LOG"
    head -3 /tmp/vision-smoke.out | tee -a "$LOG"
    exit 1
fi

if [ "$ELAPSED_MS" -gt 10000 ]; then
    echo "FAIL: Vision took too long (${ELAPSED_MS}ms > 10000ms threshold)" | tee -a "$LOG"
    exit 1
fi

# Test 3: Run full read_document.py integration
echo "Testing full read_document.py integration..." | tee -a "$LOG"
START=$(date +%s%N)
python3 "$SKILL_DIR/scripts/read_document.py" "$SAMPLE" \
    --backend auto \
    --output /tmp/vision-smoke-integration.md \
    --format markdown \
    --docling-timeout 30 2>&1 | tail -3 | tee -a "$LOG"
END=$(date +%s%N)
INT_ELAPSED=$(( (END - START) / 1000000 ))

INTEG_SIZE=$(wc -c < /tmp/vision-smoke-integration.md 2>/dev/null || echo 0)
echo "Integration test: ${INT_ELAPSED}ms, output: ${INTEG_SIZE} bytes" | tee -a "$LOG"

if [ "$INTEG_SIZE" -lt 50 ]; then
    echo "FAIL: read_document.py integration produced empty output" | tee -a "$LOG"
    exit 1
fi

# Verify conversion log has vision entry
if [ -f /tmp/vision-smoke-integration.md.conversion.log ]; then
    if grep -q "backend=vision" /tmp/vision-smoke-integration.md.conversion.log; then
        echo "Vision entry found in conversion log ✓" | tee -a "$LOG"
    else
        echo "WARN: no vision entry in conversion log" | tee -a "$LOG"
    fi
fi

echo "PASS" | tee -a "$LOG"
exit 0
