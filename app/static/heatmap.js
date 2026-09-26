(function () {
    const grid = document.getElementById("heatmap-grid");
    if (!grid) return;

    const frame = document.getElementById("heatmap-frame");
    const scroller = frame.querySelector(".heatmap-scroll");
    const tooltip = document.getElementById("heatmap-tooltip");
    const dayPanel = document.getElementById("heatmap-day");
    const region = document.getElementById("heatmap-day-region");
    const loading = region.querySelector(".heatmap-loading");
    const status = document.getElementById("heatmap-status");

    const rows = Array.from(grid.querySelectorAll('[role="row"]'));
    const matrix = rows.map((row) =>
        Array.from(row.children).map((cell) => cell.querySelector(".heatmap-cell"))
    );

    const chronological = Array.from(grid.querySelectorAll(".heatmap-cell")).sort((a, b) =>
        a.dataset.date < b.dataset.date ? -1 : 1
    );

    let current = grid.querySelector('.heatmap-cell[tabindex="0"]');
    let selected = null;
    let latestDate = null;

    scroller.scrollLeft = scroller.scrollWidth;

    function cellFor(element) {
        return element instanceof Element ? element.closest(".heatmap-cell") : null;
    }

    function dateLabel(cell) {
        return cell.getAttribute("aria-label").split(":")[0];
    }

    function setRoving(cell) {
        if (current && current !== cell) current.tabIndex = -1;
        cell.tabIndex = 0;
        current = cell;
    }

    function moveFocus(cell) {
        if (!cell) return;
        setRoving(cell);
        cell.focus();
    }

    function firstCell(row) {
        return row.find(Boolean) || null;
    }

    function lastCell(row) {
        return row.filter(Boolean).pop() || null;
    }

    function showTooltip(cell) {
        const runs = Number(cell.dataset.runs);
        const hunts = Number(cell.dataset.hunts);
        const findings = Number(cell.dataset.findings);

        tooltip.replaceChildren();
        const title = document.createElement("strong");
        title.textContent = dateLabel(cell);
        tooltip.append(title);
        for (const [label, value] of [["Runs", runs], ["Hunts", hunts], ["Marked Findings", findings]]) {
            const line = document.createElement("span");
            line.textContent = label + ": " + value;
            tooltip.append(line);
        }
        tooltip.hidden = false;

        const cellRect = cell.getBoundingClientRect();
        const frameRect = frame.getBoundingClientRect();
        const width = tooltip.offsetWidth;
        const height = tooltip.offsetHeight;
        const gap = 8;
        let left = cellRect.left - frameRect.left + cellRect.width / 2 - width / 2;
        left = Math.max(4, Math.min(left, frameRect.width - width - 4));
        let top = cellRect.top - frameRect.top - height - gap;
        if (top < 0) top = cellRect.bottom - frameRect.top + gap;
        tooltip.style.left = left + "px";
        tooltip.style.top = top + "px";
    }

    function hideTooltip() {
        tooltip.hidden = true;
    }

    function restoreKeyboardTooltip() {
        const active = document.activeElement;
        if (cellFor(active) && grid.contains(active) && active.matches(":focus-visible")) {
            showTooltip(active);
        }
    }

    function announce(message) {
        status.textContent = "";
        window.setTimeout(() => {
            status.textContent = message;
        }, 50);
    }

    function select(cell) {
        if (selected) {
            selected.classList.remove("is-selected");
            selected.parentElement.setAttribute("aria-selected", "false");
        }
        selected = cell;
        cell.classList.add("is-selected");
        cell.parentElement.setAttribute("aria-selected", "true");
        latestDate = cell.dataset.date;
        region.setAttribute("aria-busy", "true");
    }

    function showError(cell) {
        region.setAttribute("aria-busy", "false");
        dayPanel.replaceChildren();
        const box = document.createElement("div");
        box.className = "heatmap-day-result heatmap-day-error";
        box.setAttribute("role", "alert");
        const text = document.createElement("p");
        text.className = "heatmap-error-text";
        text.textContent = "Could not load runs for " + dateLabel(cell) + ".";
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "btn btn-secondary heatmap-retry";
        retry.textContent = "Try again";
        retry.addEventListener("click", () => cell.click());
        box.append(text, retry);
        dayPanel.append(box);
    }

    grid.addEventListener("keydown", (event) => {
        const cell = cellFor(event.target);
        if (!cell) return;
        const row = Number(cell.dataset.row);
        const col = Number(cell.dataset.col);
        let target = null;

        switch (event.key) {
            case "ArrowRight": target = matrix[row][col + 1]; break;
            case "ArrowLeft": target = matrix[row][col - 1]; break;
            case "ArrowDown": target = matrix[row + 1] && matrix[row + 1][col]; break;
            case "ArrowUp": target = matrix[row - 1] && matrix[row - 1][col]; break;
            case "Home": target = event.ctrlKey ? chronological[0] : firstCell(matrix[row]); break;
            case "End": target = event.ctrlKey ? chronological[chronological.length - 1] : lastCell(matrix[row]); break;
            case "Escape": hideTooltip(); return;
            default: return;
        }
        event.preventDefault();
        if (target) moveFocus(target);
    });

    grid.addEventListener("focusin", (event) => {
        const cell = cellFor(event.target);
        if (!cell) return;
        setRoving(cell);
        if (cell.matches(":focus-visible")) showTooltip(cell);
    });
    grid.addEventListener("focusout", hideTooltip);

    grid.addEventListener("mouseover", (event) => {
        const cell = cellFor(event.target);
        if (cell) showTooltip(cell);
        else hideTooltip();
    });
    grid.addEventListener("mouseleave", () => {
        hideTooltip();
        restoreKeyboardTooltip();
    });
    scroller.addEventListener("scroll", hideTooltip, { passive: true });
    document.addEventListener("click", (event) => {
        if (!frame.contains(event.target)) hideTooltip();
    });

    grid.addEventListener("click", (event) => {
        const cell = cellFor(event.target);
        if (cell) select(cell);
    });

    // hx-sync aborts superseded requests; this guard also refuses to render any
    // response that isn't for the most recently selected day.
    function isStale(detail) {
        const requester = (detail.requestConfig && detail.requestConfig.elt) || detail.elt;
        const cell = cellFor(requester);
        return !cell || cell.dataset.date !== latestDate;
    }

    document.body.addEventListener("htmx:beforeSwap", (event) => {
        if (event.detail.target !== dayPanel) return;
        if (isStale(event.detail)) {
            event.detail.shouldSwap = false;
            return;
        }
        const xhr = event.detail.xhr;
        const isHtml = (xhr.getResponseHeader("Content-Type") || "").startsWith("text/html");
        if (xhr.status >= 400 && xhr.status < 500 && isHtml) {
            event.detail.shouldSwap = true;
            event.detail.isError = false;
        }
    });

    document.body.addEventListener("htmx:beforeRequest", (event) => {
        if (!cellFor(event.detail.elt)) return;
        loading.hidden = false;
    });

    document.body.addEventListener("htmx:afterRequest", (event) => {
        const cell = cellFor(event.detail.elt);
        if (!cell || cell.dataset.date !== latestDate) return;
        loading.hidden = true;
        region.setAttribute("aria-busy", "false");
    });

    document.body.addEventListener("htmx:afterSwap", (event) => {
        if (event.detail.target !== dayPanel) return;
        const result = dayPanel.querySelector("[data-announce]");
        if (result) announce(result.dataset.announce);
    });

    for (const name of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
        document.body.addEventListener(name, (event) => {
            const cell = cellFor(event.detail.elt);
            if (cell && cell.dataset.date === latestDate) {
                showError(cell);
                announce("Could not load runs for " + dateLabel(cell) + ".");
            }
        });
    }
})();
