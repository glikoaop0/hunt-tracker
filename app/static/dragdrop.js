let draggedFromStatus = null;

document.addEventListener("dragstart", (event) => {
    const card = event.target.closest(".card");
    if (!card) return;
    draggedFromStatus = card.closest(".column")?.dataset.status ?? null;
    event.dataTransfer.setData("text/plain", card.dataset.huntId);
    event.dataTransfer.effectAllowed = "move";
});

document.addEventListener("dragover", (event) => {
    if (event.target.closest(".cards")) event.preventDefault();
});

document.addEventListener("dragenter", (event) => {
    const zone = event.target.closest(".cards");
    if (zone) zone.classList.add("drag-over");
});

document.addEventListener("dragleave", (event) => {
    const zone = event.target.closest(".cards");
    if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("drag-over");
});

document.addEventListener("drop", (event) => {
    const zone = event.target.closest(".cards");
    if (!zone) return;
    event.preventDefault();
    zone.classList.remove("drag-over");

    const huntId = event.dataTransfer.getData("text/plain");
    const status = zone.closest(".column")?.dataset.status;
    if (!huntId || !status) return;

    if (status === draggedFromStatus) return;

    if (status === "retired") {
        htmx.ajax("GET", `/hunts/${huntId}/retire-dialog`, {
            target: "#retire-dialog-root",
            swap: "innerHTML",
        });
        return;
    }

    htmx.ajax("POST", `/hunts/${huntId}/status`, {
        target: "#board",
        swap: "innerHTML",
        values: { status },
    });
});

document.addEventListener("click", (event) => {
    if (!event.target.closest(".dialog-cancel")) return;
    const root = document.getElementById("retire-dialog-root");
    if (root) root.innerHTML = "";
});

document.addEventListener("htmx:responseError", (event) => {
    const banner = document.getElementById("app-error");
    if (!banner) return;

    let message = "Something went wrong — please try again.";
    try {
        const detail = JSON.parse(event.detail.xhr.responseText).detail;
        if (typeof detail === "string") message = detail;
    } catch (_err) {
        // non-JSON error body; keep the generic message
    }

    banner.textContent = message;
    clearTimeout(banner._clearTimer);
    banner._clearTimer = setTimeout(() => {
        banner.textContent = "";
    }, 5000);
});
