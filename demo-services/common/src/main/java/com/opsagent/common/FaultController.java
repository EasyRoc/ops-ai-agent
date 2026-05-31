package com.opsagent.common;

import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.List;

@RestController
@RequestMapping("/fault")
public class FaultController {

    private volatile boolean cpuBurning = false;
    private final List<byte[]> memoryLeak = new ArrayList<>();
    private volatile boolean errorMode = false;
    private volatile long latencyMs = 0;

    @PostMapping("/cpu")
    public String cpu(@RequestParam(defaultValue = "true") boolean enable) {
        cpuBurning = enable;
        if (enable) {
            new Thread(() -> {
                while (cpuBurning) {
                    // busy loop
                }
            }).start();
        }
        return "CPU burn: " + enable;
    }

    @PostMapping("/memory")
    public String memory(@RequestParam(defaultValue = "10") int mb) {
        for (int i = 0; i < mb; i++) {
            memoryLeak.add(new byte[1024 * 1024]);
        }
        return "Allocated " + mb + " MB, total: " + memoryLeak.size() + " MB";
    }

    @PostMapping("/error")
    public String error(@RequestParam(defaultValue = "true") boolean enable) {
        errorMode = enable;
        return "Error mode: " + enable;
    }

    @PostMapping("/latency")
    public String latency(@RequestParam(defaultValue = "5000") long ms) {
        latencyMs = ms;
        return "Latency set to " + ms + "ms";
    }

    @PostMapping("/reset")
    public String reset() {
        cpuBurning = false;
        memoryLeak.clear();
        errorMode = false;
        latencyMs = 0;
        return "All faults reset";
    }

    @GetMapping("/status")
    public String status() {
        return String.format(
            "cpu=%s, memory=%dMB, error=%s, latency=%dms",
            cpuBurning, memoryLeak.size(), errorMode, latencyMs
        );
    }

    public boolean isErrorMode() { return errorMode; }
    public long getLatencyMs() { return latencyMs; }
}
