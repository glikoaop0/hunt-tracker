let dialogOpener = null;
let huntToFocusOnBoard = null;
let focusLifecycleAfterSwap = false;

function openMenus() {
    return document.querySelectorAll("details.menu[open]");
}

function closeMenu(menu, { returnFocus = false } = {}) {
    menu.open = false;
    if (returnFocus) menu.querySelector("summary")?.focus();
}

function dialogRoot() {
    return document.getElementById("retire-dialog-root");
}

function closeDialog() {
    const root = dialogRoot();
    if (root) root.innerHTML = "";
    if (dialogOpener && document.contains(dialogOpener)) dialogOpener.focus();
    dialogOpener = null;
}

function focusableIn(element) {
    return [...element.querySelectorAll("button, select, textarea, input, a[href]")].filter(
        (el) => !el.disabled && el.type !== "hidden",
    );
}

document.addEventListener("toggle", (event) => {
    const menu = event.target;
    if (!(menu instanceof HTMLDetailsElement) || !menu.classList.contains("menu") || !menu.open) return;
    openMenus().forEach((other) => {
        if (other !== menu) other.open = false;
    });
    menu.querySelector(".menu-item")?.focus();
}, true);

document.addEventListener("click", (event) => {
    openMenus().forEach((menu) => {
        if (!menu.contains(event.target)) menu.open = false;
    });

    const item = event.target.closest(".menu-item, .lifecycle-primary");
    if (!item) return;

    const menu = item.closest("details.menu");
    // The item is hidden once its menu closes, so focus must return to the toggle instead.
    const visibleOrigin = menu ? menu.querySelector("summary") : item;
    if (item.getAttribute("aria-haspopup") === "dialog") dialogOpener = visibleOrigin;
    else if (item.closest("#lifecycle")) focusLifecycleAfterSwap = true;
    if (menu) closeMenu(menu);
});

document.addEventListener("click", (event) => {
    if (event.target.closest(".dialog-cancel")) {
        dialogOpener?.focus();
        dialogOpener = null;
    }
});

document.addEventListener("keydown", (event) => {
    const dialog = document.querySelector("#retire-dialog-root .dialog");

    if (event.key === "Escape") {
        if (dialog) {
            event.preventDefault();
            closeDialog();
            return;
        }
        const menu = event.target.closest?.("details.menu[open]") ?? openMenus()[0];
        if (menu) {
            event.preventDefault();
            closeMenu(menu, { returnFocus: true });
        }
        return;
    }

    if (event.key === "Tab" && dialog) {
        const focusable = focusableIn(dialog);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!dialog.contains(document.activeElement)) {
            event.preventDefault();
            first.focus();
        } else if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }
});

document.addEventListener("submit", (event) => {
    const dialog = event.target.closest("#retire-dialog-root .dialog");
    if (!dialog) return;
    if (document.getElementById("board")) huntToFocusOnBoard = dialog.dataset.huntId;
    else focusLifecycleAfterSwap = true;
    dialogOpener = null;
});

document.addEventListener("htmx:afterSettle", (event) => {
    const root = dialogRoot();
    if (root && event.detail.target === root) {
        root.querySelector("select, textarea, button")?.focus();
        return;
    }

    if (huntToFocusOnBoard) {
        const card = document.querySelector(`.card[data-hunt-id="${huntToFocusOnBoard}"]`);
        card?.closest(".card-link")?.focus();
        huntToFocusOnBoard = null;
    }

    if (focusLifecycleAfterSwap) {
        document.getElementById("lifecycle")?.focus();
        focusLifecycleAfterSwap = false;
    }
});

document.addEventListener("htmx:responseError", () => {
    huntToFocusOnBoard = null;
    focusLifecycleAfterSwap = false;
});
