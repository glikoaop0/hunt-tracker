document.addEventListener("click", (event) => {
    const button = event.target.closest(".btn-copy");
    if (!button) return;

    const targetId = button.dataset.copyTarget;
    const target = targetId && document.getElementById(targetId);
    if (!target) return;

    const original = button.textContent;
    const resetAfterDelay = () => {
        setTimeout(() => {
            button.textContent = original;
            button.disabled = false;
        }, 1200);
    };

    navigator.clipboard
        ?.writeText(target.textContent)
        .then(() => {
            button.textContent = "Copied";
            button.disabled = true;
            resetAfterDelay();
        })
        .catch(() => {
            button.textContent = "Copy failed";
            resetAfterDelay();
        });
});
