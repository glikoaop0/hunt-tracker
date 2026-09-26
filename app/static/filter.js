document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("filter-search");
    const prioritySelect = document.getElementById("filter-priority");
    const overdueCheckbox = document.getElementById("filter-overdue");
    const clearButton = document.getElementById("filter-clear");
    const statusEl = document.getElementById("filter-status");

    if (!searchInput || !prioritySelect || !overdueCheckbox) return;

    function currentFilters() {
        return {
            search: searchInput.value.trim().toLowerCase(),
            priority: prioritySelect.value,
            overdueOnly: overdueCheckbox.checked,
        };
    }

    function applyFilters() {
        const filters = currentFilters();
        const active = Boolean(filters.search || filters.priority || filters.overdueOnly);
        let totalVisible = 0;
        let totalCards = 0;

        document.querySelectorAll(".column").forEach((column) => {
            const cards = column.querySelectorAll(".card");
            let visibleInColumn = 0;

            cards.forEach((card) => {
                totalCards += 1;
                const matches =
                    (!filters.search || (card.dataset.search || "").includes(filters.search)) &&
                    (!filters.priority || card.dataset.priority === filters.priority) &&
                    (!filters.overdueOnly || card.dataset.overdue === "true");

                const link = card.closest(".card-link");
                if (link) link.hidden = !matches;
                if (matches) {
                    visibleInColumn += 1;
                    totalVisible += 1;
                }
            });

            const countEl = column.querySelector(".count");
            if (countEl) countEl.textContent = String(visibleInColumn);

            const filteredEmpty = column.querySelector(".column-filtered-empty");
            if (filteredEmpty) {
                filteredEmpty.hidden = !(active && cards.length > 0 && visibleInColumn === 0);
            }
        });

        if (statusEl) {
            statusEl.textContent = active ? `Showing ${totalVisible} of ${totalCards} hunts` : "";
        }
    }

    searchInput.addEventListener("input", applyFilters);
    prioritySelect.addEventListener("change", applyFilters);
    overdueCheckbox.addEventListener("change", applyFilters);

    clearButton?.addEventListener("click", () => {
        searchInput.value = "";
        prioritySelect.value = "";
        overdueCheckbox.checked = false;
        applyFilters();
    });

    // Board content is replaced wholesale on status moves, retirement and
    // reactivation (all go through htmx). Re-apply the current filters to
    // whatever DOM htmx just swapped in, rather than attaching new listeners.
    document.body.addEventListener("htmx:afterSwap", applyFilters);

    applyFilters();
});
