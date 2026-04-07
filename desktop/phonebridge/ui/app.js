/**
 * PhoneBridge Desktop — Frontend Application
 *
 * Communicates with the Python backend via pywebview JS bridge.
 * Polls for device/mount state and renders the UI dynamically.
 */

// ─── State ────────────────────────────────────────────────

let devices = [];
let mounts = {};
let settings = {};
let pollTimer = null;
let pendingMountDeviceId = null;

// ─── Initialization ───────────────────────────────────────

window.addEventListener("pywebviewready", () => {
    console.log("pywebview bridge ready");
    init();
});

async function init() {
    // Load initial data
    await refreshAll();
    await loadSettings();

    // Start polling every 2 seconds
    pollTimer = setInterval(refreshAll, 2000);

    // Wire up event handlers
    document.getElementById("btn-rescan").addEventListener("click", rescan);
    document.getElementById("btn-settings").addEventListener("click", openSettings);
    document.getElementById("btn-close-settings").addEventListener("click", closeSettings);
    document.getElementById("settings-overlay").addEventListener("click", closeSettings);
    document.getElementById("password-overlay").addEventListener("click", closePasswordModal);
    document.getElementById("btn-password-cancel").addEventListener("click", closePasswordModal);
    document.getElementById("btn-password-submit").addEventListener("click", submitPassword);
    document.getElementById("password-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") submitPassword();
        if (e.key === "Escape") closePasswordModal();
    });

    // Settings toggles
    document.getElementById("toggle-startup").addEventListener("change", async (e) => {
        await pywebview.api.toggle_startup(e.target.checked);
    });
    document.getElementById("toggle-notifications").addEventListener("change", async (e) => {
        await pywebview.api.set_notifications(e.target.checked);
    });
    document.getElementById("select-cache-mode").addEventListener("change", async (e) => {
        await pywebview.api.set_cache_mode(e.target.value);
    });
}

// ─── Data Fetching ────────────────────────────────────────

async function refreshAll() {
    try {
        const state = await pywebview.api.get_state();
        devices = state.devices || [];
        mounts = state.mounts || {};
        updateStatusBar(state);
        renderDevices();
    } catch (e) {
        console.error("Failed to refresh:", e);
    }
}

async function loadSettings() {
    try {
        settings = await pywebview.api.get_settings();
        document.getElementById("toggle-startup").checked = settings.start_with_windows || false;
        document.getElementById("toggle-notifications").checked = settings.show_notifications !== false;
        document.getElementById("select-cache-mode").value = settings.vfs_cache_mode || "full";

        // Dependencies
        const deps = await pywebview.api.get_dependencies();
        updateDep("dep-rclone", deps.rclone);
        updateDep("dep-winfsp", deps.winfsp);

        // Update status bar dep text
        const allOk = deps.rclone && deps.winfsp;
        document.getElementById("deps-text").textContent = allOk ? "All OK" : "Missing deps!";
        document.getElementById("deps-text").style.color = allOk ? "var(--accent-green)" : "var(--accent-red)";
    } catch (e) {
        console.error("Failed to load settings:", e);
    }
}

function updateDep(elementId, installed) {
    const el = document.getElementById(elementId);
    const icon = el.querySelector(".dep-icon");
    const status = el.querySelector(".dep-status");
    if (installed) {
        icon.textContent = "✅";
        status.textContent = "Installed";
        status.style.color = "var(--accent-green)";
    } else {
        icon.textContent = "❌";
        status.textContent = "Not found";
        status.style.color = "var(--accent-red)";
    }
}

// ─── Status Bar ───────────────────────────────────────────

function updateStatusBar(state) {
    const deviceCount = devices.length;
    const mountCount = Object.keys(mounts).length;

    document.getElementById("device-count").textContent =
        `${deviceCount} device${deviceCount !== 1 ? "s" : ""}`;
    document.getElementById("mount-count").textContent =
        `${mountCount} mounted`;

    const dot = document.getElementById("scan-dot");
    const scanText = document.getElementById("scan-status");

    if (mountCount > 0) {
        dot.className = "status-dot";
        scanText.textContent = "Connected";
    } else if (deviceCount > 0) {
        dot.className = "status-dot";
        dot.style.background = "var(--accent-orange)";
        scanText.textContent = "Devices found";
    } else {
        dot.className = "status-dot";
        dot.style.background = "";
        scanText.textContent = "Scanning...";
    }
}

// ─── Device Rendering ─────────────────────────────────────

function renderDevices() {
    const container = document.getElementById("devices-list");
    const empty = document.getElementById("empty-state");

    if (devices.length === 0) {
        container.innerHTML = "";
        container.appendChild(createEmptyState());
        return;
    }

    // Build new cards (preserve existing ones to avoid flicker)
    const existingIds = new Set();
    const fragment = document.createDocumentFragment();

    devices.forEach((device) => {
        existingIds.add(device.device_id);
        const mount = mounts[device.device_id] || null;
        const card = createDeviceCard(device, mount);
        fragment.appendChild(card);
    });

    container.innerHTML = "";
    container.appendChild(fragment);
}

function createEmptyState() {
    const div = document.createElement("div");
    div.className = "empty-state";
    div.id = "empty-state";
    div.innerHTML = `
        <div class="empty-icon">📡</div>
        <p>Scanning for phones on your network...</p>
        <p class="empty-hint">Make sure PhoneBridge is running on your Android device</p>
    `;
    return div;
}

function createDeviceCard(device, mount) {
    const isMounted = mount && mount.is_alive;
    const card = document.createElement("div");
    card.className = `device-card${isMounted ? " mounted" : ""}`;
    card.dataset.deviceId = device.device_id;

    const badgeClass = isMounted ? "badge-mounted" : "badge-discovered";
    const badgeText = isMounted ? `✓ Mounted (${mount.drive_letter})` : "Discovered";
    const authIcon = device.auth_required ? "🔒" : "🔓";
    const protocol = (device.protocol || "http").toUpperCase();

    let actionsHtml = "";
    if (isMounted) {
        actionsHtml = `
            <button class="btn btn-success" onclick="openExplorer('${device.device_id}')">
                <span class="btn-icon">📂</span> Open Explorer
            </button>
            <button class="btn btn-danger" onclick="unmountDevice('${device.device_id}')">
                <span class="btn-icon">⏏️</span> Unmount
            </button>
            <button class="btn btn-ghost" onclick="changePassword('${device.device_id}')">
                <span class="btn-icon">🔑</span> Change Password
            </button>
        `;
    } else {
        if (device.auth_required) {
            actionsHtml = `
                <button class="btn btn-primary" onclick="promptAndMount('${device.device_id}')">
                    <span class="btn-icon">🔑</span> Enter Code & Mount
                </button>
            `;
        } else {
            actionsHtml = `
                <button class="btn btn-primary" onclick="mountDevice('${device.device_id}', '')">
                    <span class="btn-icon">💾</span> Mount Drive
                </button>
            `;
        }
    }

    card.innerHTML = `
        <div class="device-header">
            <div class="device-name">
                <span class="device-emoji">📱</span>
                <h3>${escapeHtml(device.display_name)}</h3>
            </div>
            <span class="device-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="device-details">
            <span class="detail"><span class="detail-icon">🌐</span> ${device.ip_address}:${device.port}</span>
            <span class="detail"><span class="detail-icon">${authIcon}</span> ${device.auth_required ? "Auth Required" : "Open"}</span>
            <span class="detail"><span class="detail-icon">🔐</span> ${protocol}</span>
            ${device.device_model ? `<span class="detail"><span class="detail-icon">📋</span> ${escapeHtml(device.device_model)}</span>` : ""}
            ${isMounted ? `<span class="detail"><span class="detail-icon">💾</span> Drive ${mount.drive_letter}</span>` : ""}
        </div>
        <div class="device-actions">${actionsHtml}</div>
    `;

    return card;
}

// ─── Actions ──────────────────────────────────────────────

async function mountDevice(deviceId, password) {
    try {
        const result = await pywebview.api.mount_device(deviceId, password);
        if (result.error) {
            if (result.error === "auth_failed") {
                showPasswordError("Incorrect password. Check the code on your phone.");
            } else if (result.error === "unreachable") {
                alert("Server not reachable. Make sure PhoneBridge is running on your phone.");
            } else {
                alert("Mount failed: " + result.message);
            }
        } else {
            closePasswordModal();
            await refreshAll();
        }
    } catch (e) {
        alert("Error: " + e);
    }
}

async function unmountDevice(deviceId) {
    try {
        await pywebview.api.unmount_device(deviceId);
        await refreshAll();
    } catch (e) {
        alert("Unmount failed: " + e);
    }
}

async function openExplorer(deviceId) {
    try {
        await pywebview.api.open_explorer(deviceId);
    } catch (e) {
        console.error("Failed to open explorer:", e);
    }
}

function promptAndMount(deviceId) {
    pendingMountDeviceId = deviceId;
    const device = devices.find(d => d.device_id === deviceId);
    const name = device ? device.display_name : "phone";

    document.getElementById("password-prompt").textContent =
        `Enter the connection code displayed on ${name}.`;
    document.getElementById("password-input").value = "";
    document.getElementById("password-error").classList.add("hidden");
    document.getElementById("password-modal").classList.remove("hidden");

    setTimeout(() => document.getElementById("password-input").focus(), 100);
}

function changePassword(deviceId) {
    promptAndMount(deviceId);
}

async function submitPassword() {
    const password = document.getElementById("password-input").value.trim();
    if (!password) return;

    const btn = document.getElementById("btn-password-submit");
    btn.classList.add("loading");
    btn.disabled = true;

    await mountDevice(pendingMountDeviceId, password);

    btn.classList.remove("loading");
    btn.disabled = false;
}

function showPasswordError(msg) {
    const err = document.getElementById("password-error");
    err.textContent = msg;
    err.classList.remove("hidden");
    document.getElementById("password-input").select();
}

function closePasswordModal() {
    document.getElementById("password-modal").classList.add("hidden");
    pendingMountDeviceId = null;
}

async function rescan() {
    const btn = document.getElementById("btn-rescan");
    btn.classList.add("loading");
    btn.disabled = true;

    try {
        await pywebview.api.rescan();
    } catch (e) {
        console.error("Rescan failed:", e);
    }

    setTimeout(() => {
        btn.classList.remove("loading");
        btn.disabled = false;
        refreshAll();
    }, 2000);
}

// ─── Settings Panel ───────────────────────────────────────

function openSettings() {
    loadSettings();
    document.getElementById("settings-panel").classList.remove("hidden");
}

function closeSettings() {
    document.getElementById("settings-panel").classList.add("hidden");
}

// ─── Utilities ────────────────────────────────────────────

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
