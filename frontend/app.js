/* ==========================================================================
   Adaptive Traffic Protection System — Interactive Dashboard Application Logic
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // --- UI Elements ---
    const targetEndpointSelect = document.getElementById("target-endpoint");
    const btnSingleReq = document.getElementById("btn-single-req");
    const btnHealthCheck = document.getElementById("btn-health-check");
    const btnClearLogs = document.getElementById("btn-clear-logs");

    const btnLoadPresets = document.querySelectorAll(".btn-load");
    const toggleStream = document.getElementById("toggle-stream");
    const streamRateSlider = document.getElementById("stream-rate-slider");
    const streamRateVal = document.getElementById("stream-rate-val");

    const gatewayState = document.getElementById("gateway-state");
    const backendState = document.getElementById("backend-state");

    const totalAcceptedEl = document.getElementById("total-accepted");
    const totalRejectedEl = document.getElementById("total-rejected");
    const avgLatencyEl = document.getElementById("avg-latency");

    const terminalLogs = document.getElementById("terminal-logs");
    const pipe1Packets = document.getElementById("pipe1-packets");
    const pipe2Packets = document.getElementById("pipe2-packets");

    // --- Metrics State ---
    let totalAccepted = 0;
    let totalRejected = 0;
    let totalRequests = 0;
    let totalLatencySum = 0;

    let rpsAcceptedCurrentWindow = 0;
    let rpsRejectedCurrentWindow = 0;

    let streamInterval = null;

    // --- Initialize Charts ---
    const trafficChartCtx = document.getElementById("traffic-chart").getContext("2d");
    const latencyChartCtx = document.getElementById("latency-chart").getContext("2d");

    const maxDataPoints = 20;
    const initialLabels = Array.from({ length: maxDataPoints }, (_, i) => `${maxDataPoints - i}s ago`);

    // Traffic RPS Chart (Accepted vs Rejected)
    const trafficChart = new Chart(trafficChartCtx, {
        type: 'line',
        data: {
            labels: initialLabels,
            datasets: [
                {
                    label: 'Accepted (HTTP 200)',
                    data: Array(maxDataPoints).fill(0),
                    borderColor: '#10B981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2
                },
                {
                    label: 'Rejected (HTTP 429)',
                    data: Array(maxDataPoints).fill(0),
                    borderColor: '#EF4444',
                    backgroundColor: 'rgba(239, 68, 68, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: {
                    labels: { color: '#9CA3AF', font: { family: 'Inter', size: 11 } }
                }
            },
            scales: {
                x: { ticks: { color: '#6B7280', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#6B7280', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' }, beginAtZero: true }
            }
        }
    });

    // Latency Chart
    const latencyChart = new Chart(latencyChartCtx, {
        type: 'line',
        data: {
            labels: initialLabels,
            datasets: [
                {
                    label: 'Latency (ms)',
                    data: Array(maxDataPoints).fill(0),
                    borderColor: '#06B6D4',
                    backgroundColor: 'rgba(6, 182, 212, 0.15)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { labels: { color: '#9CA3AF', font: { family: 'Inter', size: 11 } } }
            },
            scales: {
                x: { ticks: { color: '#6B7280', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#6B7280', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' }, beginAtZero: true }
            }
        }
    });

    // --- Logging Utility ---
    function logMessage(text, type = "info") {
        const timeStr = new Date().toLocaleTimeString();
        const line = document.createElement("div");
        line.className = `log-line ${type}`;
        line.innerHTML = `<span class="log-time">[${timeStr}]</span><span>${escapeHtml(text)}</span>`;
        terminalLogs.appendChild(line);
        terminalLogs.scrollTop = terminalLogs.scrollHeight;
    }

    function escapeHtml(str) {
        return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    // --- Topology Packet Animator ---
    function animateTopologyPacket(isAccepted) {
        // Pipe 1 packet (Client -> Gateway)
        const p1 = document.createElement("div");
        p1.className = `packet ${isAccepted ? 'accepted' : 'rejected'}`;
        pipe1Packets.appendChild(p1);

        setTimeout(() => p1.remove(), 1000);

        // If accepted, show Pipe 2 packet (Gateway -> Backend)
        if (isAccepted) {
            setTimeout(() => {
                const p2 = document.createElement("div");
                p2.className = "packet accepted";
                pipe2Packets.appendChild(p2);
                setTimeout(() => p2.remove(), 1000);
            }, 500);
        }
    }

    // --- Send HTTP Request Logic ---
    async function executeRequest(url) {
        const startTime = performance.now();
        totalRequests++;

        try {
            const response = await fetch(url, { method: 'GET' });
            const endTime = performance.now();
            const latency = Math.round(endTime - startTime);

            totalLatencySum += latency;
            const status = response.status;

            if (status === 200) {
                totalAccepted++;
                rpsAcceptedCurrentWindow++;
                totalAcceptedEl.textContent = totalAccepted;
                animateTopologyPacket(true);

                const data = await response.json();
                logMessage(`HTTP 200 OK (${latency}ms) — ${data.endpoint || url} — Service: ${data.service || 'OK'}`, "success");
            } else if (status === 429) {
                totalRejected++;
                rpsRejectedCurrentWindow++;
                totalRejectedEl.textContent = totalRejected;
                animateTopologyPacket(false);

                logMessage(`HTTP 429 Too Many Requests (${latency}ms) — [Static Token Bucket Limit Exceeded!]`, "error");
            } else {
                logMessage(`HTTP ${status} (${latency}ms)`, "warn");
            }

            // Update average latency
            const avg = Math.round(totalLatencySum / totalRequests);
            avgLatencyEl.textContent = `${avg} ms`;

        } catch (err) {
            const endTime = performance.now();
            const latency = Math.round(endTime - startTime);
            logMessage(`Request Failed: ${err.message} (${latency}ms)`, "error");
        }
    }

    // --- Single Request Button Handler ---
    btnSingleReq.addEventListener("click", () => {
        executeRequest(targetEndpointSelect.value);
    });

    // --- Health Check Button Handler ---
    btnHealthCheck.addEventListener("click", async () => {
        logMessage("Executing health checks across system topology...", "info");
        checkSystemHealth();
    });

    // --- Clear Logs Handler ---
    btnClearLogs.addEventListener("click", () => {
        terminalLogs.innerHTML = "";
        logMessage("Console logs cleared.", "info");
    });

    // --- Load Generator Presets (Burst Requests) ---
    btnLoadPresets.forEach(btn => {
        btn.addEventListener("click", () => {
            const rate = parseInt(btn.dataset.rate, 10);
            logMessage(`Triggering burst load of ${rate} requests...`, "warn");

            for (let i = 0; i < rate; i++) {
                setTimeout(() => {
                    executeRequest(targetEndpointSelect.value);
                }, i * 15); // Spread over 300ms window
            }
        });
    });

    // --- Stream Generator Logic ---
    streamRateSlider.addEventListener("input", (e) => {
        streamRateVal.textContent = `${e.target.value} RPS`;
        if (toggleStream.checked) {
            restartStream();
        }
    });

    toggleStream.addEventListener("change", () => {
        if (toggleStream.checked) {
            logMessage(`Started continuous stream generator at ${streamRateSlider.value} RPS.`, "info");
            startStream();
        } else {
            logMessage("Stopped continuous stream generator.", "info");
            stopStream();
        }
    });

    function startStream() {
        stopStream();
        const rps = parseInt(streamRateSlider.value, 10);
        const intervalMs = 1000 / rps;

        streamInterval = setInterval(() => {
            executeRequest(targetEndpointSelect.value);
        }, intervalMs);
    }

    function stopStream() {
        if (streamInterval) {
            clearInterval(streamInterval);
            streamInterval = null;
        }
    }

    function restartStream() {
        if (toggleStream.checked) {
            startStream();
        }
    }

    // --- Check System Health Periodic Job ---
    async function checkSystemHealth() {
        // Gateway check (8082)
        try {
            const gwRes = await fetch("http://localhost:8082/actuator/health");
            if (gwRes.ok) {
                gatewayState.textContent = "ONLINE (8082)";
                gatewayState.style.color = "#10B981";
            } else {
                gatewayState.textContent = "UNHEALTHY";
                gatewayState.style.color = "#EF4444";
            }
        } catch (e) {
            gatewayState.textContent = "OFFLINE";
            gatewayState.style.color = "#EF4444";
        }

        // Backend check (8081)
        try {
            const bkRes = await fetch("http://localhost:8081/actuator/health");
            if (bkRes.ok) {
                backendState.textContent = "ONLINE (8081)";
                backendState.style.color = "#10B981";
            } else {
                backendState.textContent = "UNHEALTHY";
                backendState.style.color = "#EF4444";
            }
        } catch (e) {
            backendState.textContent = "OFFLINE";
            backendState.style.color = "#EF4444";
        }
    }

    // Initial Health Check
    checkSystemHealth();
    setInterval(checkSystemHealth, 10000);

    // --- Real-time Chart Tick Loop (1-second sliding window) ---
    setInterval(() => {
        // Shift chart data
        trafficChart.data.datasets[0].data.shift();
        trafficChart.data.datasets[0].data.push(rpsAcceptedCurrentWindow);

        trafficChart.data.datasets[1].data.shift();
        trafficChart.data.datasets[1].data.push(rpsRejectedCurrentWindow);

        trafficChart.update();

        // Calculate average latency for this window
        const avgLat = totalRequests > 0 ? Math.round(totalLatencySum / totalRequests) : 0;
        latencyChart.data.datasets[0].data.shift();
        latencyChart.data.datasets[0].data.push(avgLat);
        latencyChart.update();

        // Reset current window counters
        rpsAcceptedCurrentWindow = 0;
        rpsRejectedCurrentWindow = 0;
    }, 1000);
});
