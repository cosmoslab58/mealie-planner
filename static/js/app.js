/* Mealie Companion front-end. Server-injected config lives on window.APP:
   { week: 'current'|'next', recipeMap: {displayName: recipeId} } */
(function () {
    'use strict';

    const APP = window.APP || { week: 'current', recipeMap: {} };
    const recipeMap = APP.recipeMap;

    // --- AI activity indicator (non-blocking pill) ---
    // Counter-based so overlapping AI requests keep the pill visible until the last finishes.
    let aiBusyCount = 0;
    function aiBusy(label) {
        aiBusyCount++;
        if (label) document.getElementById('ai-pill-text').innerText = label;
        document.getElementById('ai-pill').classList.add('show');
    }
    function aiIdle() {
        aiBusyCount = Math.max(0, aiBusyCount - 1);
        if (aiBusyCount === 0) document.getElementById('ai-pill').classList.remove('show');
    }

    function toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
    }

    function toggleDrawer() {
        document.getElementById('sidebar').classList.toggle('active');
        document.getElementById('drawer-overlay').classList.toggle('active');
    }

    function toggleChat() {
        document.getElementById('chat-window').classList.toggle('open');
    }

    function switchTab(id) {
        if (!document.getElementById('tab-' + id)) id = 'menu';
        document.querySelectorAll('.tab-view').forEach(v => v.style.display = 'none');
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        const view = document.getElementById('tab-' + id);
        if (view) view.style.display = 'block';
        const nav = document.getElementById('nav-' + id);
        if (nav) nav.classList.add('active');
        localStorage.setItem('activeTab', id);
        if (window.innerWidth <= 1024) toggleDrawer();
    }

    function openStaplesModal() { document.getElementById('staples-modal').style.display = 'flex'; }
    function closeStaplesModal() { document.getElementById('staples-modal').style.display = 'none'; }

    async function deleteStaple(itemId, btn) {
        if (!confirm("Are you sure you want to delete this staple from your larder list?")) return;
        try {
            const res = await fetch('/delete-staple', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: itemId })
            });
            if (res.ok) {
                btn.closest('div').remove();
            } else {
                const data = await res.json().catch(() => ({}));
                alert("Error deleting staple: " + (data.error || "Unknown error"));
            }
        } catch (err) {
            console.error(err);
            alert("Connection error.");
        }
    }

    async function addStaple() {
        const input = document.getElementById('new-staple-input');
        const val = input.value.trim();
        if (!val) return;
        try {
            const res = await fetch('/add-staple', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note: val })
            });
            if (res.ok) {
                sessionStorage.setItem('open_staples_modal', 'true');
                window.location.reload();
            } else {
                const data = await res.json().catch(() => ({}));
                alert("Error adding staple: " + (data.error || "Unknown error"));
            }
        } catch (err) {
            console.error(err);
            alert("Connection error.");
        }
    }

    function toggleEmailValue() {
        const hiddenInput = document.getElementById('emails-enabled-hidden');
        const toggleBtn = document.getElementById('email-toggle-btn');
        const statusLabel = document.getElementById('email-status-label');

        if (hiddenInput.value === '1') {
            hiddenInput.value = '0';
            toggleBtn.innerText = 'Enable Emails';
            toggleBtn.className = 'btn btn-primary';
            statusLabel.innerText = 'Currently: Disabled (Pending Save)';
        } else {
            hiddenInput.value = '1';
            toggleBtn.innerText = 'Disable Emails';
            toggleBtn.className = 'btn btn-secondary';
            statusLabel.innerText = 'Currently: Enabled (Pending Save)';
        }
        statusLabel.style.color = 'var(--color-accent)';
    }

    function updateShoppingListLayout() {
        const checkedContainer = document.getElementById('checked-items-container');
        const checkedList = document.getElementById('checked-items-list');
        if (!checkedContainer || !checkedList) return;

        document.querySelectorAll('.shopping-item').forEach(item => {
            const cb = item.querySelector('.item-checkbox');
            if (cb && cb.checked) {
                if (item.parentElement !== checkedList) checkedList.appendChild(item);
            } else {
                const catName = item.getAttribute('data-category');
                const group = document.querySelector(`.category-group[data-category="${catName}"]`);
                if (group) {
                    const itemsDiv = group.querySelector('.category-items');
                    if (itemsDiv && item.parentElement !== itemsDiv) itemsDiv.appendChild(item);
                }
            }
        });

        // Hide or show category groups depending on whether they have items left
        document.querySelectorAll('.category-group[data-category]').forEach(group => {
            const itemsDiv = group.querySelector('.category-items');
            group.style.display = (itemsDiv && itemsDiv.querySelector('.shopping-item')) ? '' : 'none';
        });

        checkedContainer.style.display = checkedList.querySelector('.shopping-item') ? '' : 'none';
    }

    function withButtonBusy(btn, busyText, doneText, fn) {
        const originalText = btn.innerText;
        btn.innerText = busyText;
        btn.disabled = true;
        return fn()
            .then(ok => {
                btn.innerText = ok ? doneText : originalText;
                if (ok) {
                    setTimeout(() => { btn.innerText = originalText; btn.disabled = false; }, 2000);
                } else {
                    btn.disabled = false;
                }
            })
            .catch(err => {
                console.error(err);
                alert("Connection error.");
                btn.innerText = originalText;
                btn.disabled = false;
            });
    }

    function checkAllItems(btn) {
        if (!confirm("Check off all items on this list?")) return;
        withButtonBusy(btn, "Checking...", "All Checked!", async () => {
            const res = await fetch('/check-all-items', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ week: APP.week })
            });
            const data = await res.json();
            if (!data.success) {
                alert("Error checking items: " + data.error);
                return false;
            }
            document.querySelectorAll('.shopping-list-checkbox').forEach(cb => {
                cb.checked = true;
                const textSpan = cb.parentElement.querySelector('.item-text');
                if (textSpan) textSpan.classList.add('checked');
            });
            updateShoppingListLayout();
            return true;
        });
    }

    function deleteCheckedItems(btn) {
        const checkedCheckboxes = document.querySelectorAll('.shopping-list-checkbox:checked');
        if (checkedCheckboxes.length === 0) {
            alert("No items are checked!");
            return;
        }
        if (!confirm(`Delete all ${checkedCheckboxes.length} checked item(s) from this shopping list?`)) return;
        withButtonBusy(btn, "Deleting...", "Deleted!", async () => {
            const res = await fetch('/delete-checked-items', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ week: APP.week })
            });
            const data = await res.json();
            if (!data.success) {
                alert("Error deleting checked items: " + data.error);
                return false;
            }
            checkedCheckboxes.forEach(cb => {
                const itemDiv = cb.closest('.shopping-item');
                if (itemDiv) itemDiv.remove();
            });
            updateShoppingListLayout();
            return true;
        });
    }

    function copyShoppingList(btn) {
        const lines = [];
        document.querySelectorAll('.category-group[data-category]').forEach(group => {
            const title = group.querySelector('.category-title');
            const uncheckedItems = [];
            group.querySelectorAll('.shopping-item').forEach(item => {
                const cb = item.querySelector('.item-checkbox');
                if (cb && !cb.checked) {
                    uncheckedItems.push('- ' + item.querySelector('.item-text').innerText.trim());
                }
            });
            if (uncheckedItems.length > 0) {
                if (title) lines.push(title.innerText.trim().toUpperCase());
                lines.push(...uncheckedItems);
                lines.push('');
            }
        });

        if (!lines.length) return alert("No active items to copy.");
        if (lines[lines.length - 1] === '') lines.pop();

        navigator.clipboard.writeText(lines.join('\n')).then(() => {
            const old = btn.innerText;
            btn.innerText = "Copied!";
            setTimeout(() => btn.innerText = old, 2000);
        });
    }

    function printShoppingList() { window.print(); }

    async function quickAddShoppingItem() {
        const val = document.getElementById('quick-add-input').value.trim();
        if (!val) return;
        try {
            const res = await fetch('/add-shopping-item', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note: val, week: APP.week })
            });
            if (res.ok) {
                window.location.reload();
            } else {
                const data = await res.json().catch(() => ({}));
                alert("Error adding item: " + (data.error || "Unknown error"));
            }
        } catch (err) {
            console.error(err);
            alert("Connection error.");
        }
    }

    // --- Swap recommendations (shared datalist) ---
    // A single #recipe-options datalist holds every recipe; per-day AI picks are
    // cached and swapped into the top of the list when that day's input focuses.
    const swapRecsCache = {};

    function renderSwapSuggestions(date) {
        const datalist = document.getElementById('recipe-options');
        if (!datalist) return;
        datalist.querySelectorAll('option[data-star]').forEach(opt => opt.remove());
        (swapRecsCache[date] || []).forEach(rec => {
            const label = `⭐ ${rec.name}`;
            recipeMap[label] = rec.id;
            const opt = document.createElement('option');
            opt.value = label;
            opt.dataset.star = '1';
            datalist.insertBefore(opt, datalist.firstChild);
        });
    }

    async function loadSwapRecommendations(inputEl) {
        const date = inputEl.dataset.date;
        if (swapRecsCache[date]) {
            renderSwapSuggestions(date);
            return;
        }
        if (inputEl.dataset.loading) return;
        inputEl.dataset.loading = "true";

        inputEl.classList.add('ai-busy');
        aiBusy('Finding tasty swaps…');
        try {
            const res = await fetch(`/get-swap-recommendations?date=${date}`);
            if (res.ok) {
                swapRecsCache[date] = await res.json() || [];
                renderSwapSuggestions(date);
                
                // Programmatically re-show the dropdown picker with a small delay
                // to prevent synchronous class/DOM changes from auto-dismissing it.
                setTimeout(() => {
                    if (document.activeElement === inputEl) {
                        if (typeof inputEl.showPicker === 'function') {
                            try {
                                inputEl.showPicker();
                            } catch (e) {
                                inputEl.blur();
                                inputEl.focus();
                            }
                        } else {
                            inputEl.blur();
                            inputEl.focus();
                        }
                    }
                }, 100);
            }
        } catch (err) {
            console.error("Error loading swap recommendations", err);
        } finally {
            delete inputEl.dataset.loading;
            inputEl.classList.remove('ai-busy');
            aiIdle();
        }
    }

    // Selecting a swap/skip triggers a server-side meal plan update, then a reload.
    function onSwapSelected(inputEl) {
        const recipeId = recipeMap[inputEl.value];
        if (!recipeId) {
            inputEl.value = '';  // unrecognized entry; clear it back to the placeholder
            return;
        }
        localStorage.setItem('shopping_list_dirty', 'true');
        inputEl.form.querySelector('.select-recipe-id').value = recipeId;
        showLoading('Updating Menu', 'Saving your selection…');
        inputEl.form.submit();
    }

    function showLoading(msg, sub = '') {
        document.getElementById('loading-overlay').style.display = 'flex';
        document.getElementById('loading-text').innerText = msg;
        document.getElementById('loading-log').innerText = sub;
    }

    function submitPlanForm(e) {
        e.preventDefault();
        const params = new URLSearchParams(new FormData(e.target));
        showLoading('Curating Menu', 'Initializing agent...');
        const es = new EventSource(`/plan-stream?${params.toString()}`);
        let progress = 0;
        es.onmessage = (ev) => {
            const d = JSON.parse(ev.data);
            if (typeof d.progress === 'number') progress = d.progress;
            if (d.status === 'complete') {
                es.close();
                if (d.warning) {
                    localStorage.setItem('shopping_list_dirty', 'true');
                    window.location.href = `/?week=${APP.week}&error_msg=` + encodeURIComponent(d.warning);
                } else {
                    localStorage.setItem('shopping_list_dirty', 'false');
                    window.location.href = `/?week=${APP.week}&success_msg=Menu Ready`;
                }
            } else {
                document.getElementById('loading-log').innerText = d.status || '';
            }
        };
        // The server closing the stream can surface as an error even when the run finished
        // (seen 2026-10-10 on Firefox Android behind the tunnel). Generation runs server-side
        // and keeps going without this connection, so a drop is never a failed plan by itself.
        es.onerror = () => {
            es.close();
            localStorage.setItem('shopping_list_dirty', 'true');
            const msg = progress >= 99
                ? 'success_msg=Menu Ready'
                : 'error_msg=' + encodeURIComponent('Lost the connection while planning. The plan is still being made; reload in a minute.');
            window.location.href = `/?week=${APP.week}&${msg}`;
        };
    }

    function initChat() {
        const cin = document.getElementById('chat-input');
        const csend = document.getElementById('chat-send');
        const cmessages = document.getElementById('chat-messages');
        let history = [];
        let messages = [];

        function addMsg(s, t, save = true) {
            const d = document.createElement('div');
            d.className = `chat-msg ${s === 'user' ? 'user' : 'bot'}`;
            if (s === 'bot' && typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
                d.innerHTML = DOMPurify.sanitize(marked.parse(t));
            } else {
                d.textContent = t;
            }
            cmessages.appendChild(d);
            cmessages.scrollTop = cmessages.scrollHeight;
            if (save) messages.push({ s, t });
        }

        function showTyping() {
            const d = document.createElement('div');
            d.id = 'typing-indicator';
            d.className = 'typing';
            d.innerHTML = '<span></span><span></span><span></span>';
            cmessages.appendChild(d);
            cmessages.scrollTop = cmessages.scrollHeight;
        }

        function hideTyping() {
            const d = document.getElementById('typing-indicator');
            if (d) d.remove();
        }

        async function loadHistoryFromServer() {
            try {
                const r = await fetch('/chat-history');
                if (r.ok) {
                    const data = await r.json();
                    history = data.history || [];
                    messages = data.messages || [];
                    cmessages.innerHTML = '';
                    messages.forEach(m => addMsg(m.s, m.t, false));
                }
            } catch (e) {
                console.error("Error loading chat history from server", e);
            }
        }

        loadHistoryFromServer();

        // Auto-sync history when the tab becomes visible again (e.g. phone wakes up)
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') loadHistoryFromServer();
        });
        window.addEventListener('focus', loadHistoryFromServer);

        document.getElementById('chat-clear').onclick = async () => {
            if (confirm("Clear history?")) {
                history = [];
                messages = [];
                cmessages.innerHTML = '';
                await fetch('/chat-clear', { method: 'POST' });
            }
        };

        cin.oninput = () => csend.disabled = !cin.value.trim();
        cin.onkeydown = (e) => {
            if (e.key === 'Enter' && !csend.disabled) csend.click();
        };
        csend.onclick = async () => {
            const t = cin.value.trim();
            addMsg('user', t);
            cin.value = '';
            csend.disabled = true;

            showTyping();
            // Global cue when the chat panel isn't visible (typing dots cover the open case).
            const chatVisible = document.getElementById('chat-window').classList.contains('open');
            if (!chatVisible) aiBusy('Chef is replying…');

            try {
                const r = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: t, week: APP.week })
                });
                const res = await r.json();
                if (res.success) {
                    await loadHistoryFromServer();
                    if (res.plan_changed) {
                        showLoading('Refreshing Kitchen', 'Updating your menu and shopping list…');
                        setTimeout(() => { window.location.href = `/?week=${APP.week}`; }, 1200);
                    }
                } else {
                    addMsg('bot', 'Chef is having some technical difficulties. Please try again.');
                }
            } catch (e) {
                console.warn("POST /chat timed out or disconnected, syncing from server history...", e);
                await loadHistoryFromServer();
            } finally {
                hideTyping();
                if (!chatVisible) aiIdle();
            }
        };
    }

    function updateSyncIndicator() {
        const ind = document.getElementById('sync-indicator');
        if (!ind) return;
        const isDirty = localStorage.getItem('shopping_list_dirty') === 'true';
        if (isDirty) {
            ind.className = 'sync-dot dirty';
            ind.title = "You have unsynced changes! Click Refresh List to update Mealie groceries.";
        } else {
            ind.className = 'sync-dot synced';
            ind.title = "Shopping list is synced";
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('success_msg')) {
            const msg = urlParams.get('success_msg').toLowerCase();
            if (msg.includes('ready') || msg.includes('recalculated') || msg.includes('cleared')) {
                localStorage.setItem('shopping_list_dirty', 'false');
            }
        }
        updateSyncIndicator();

        if (sessionStorage.getItem('open_staples_modal') === 'true') {
            sessionStorage.removeItem('open_staples_modal');
            openStaplesModal();
        }
        switchTab(localStorage.getItem('activeTab') || 'menu');
        document.querySelectorAll('.shopping-list-checkbox').forEach(cb => {
            cb.onchange = async function () {
                this.parentElement.querySelector('.item-text').classList.toggle('checked', this.checked);
                updateShoppingListLayout();
                await fetch('/toggle-shopping-item', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item_id: this.dataset.id, checked: this.checked, week: APP.week })
                });
            };
        });
        updateShoppingListLayout();
        initChat();
    });

    // Expose handlers used by inline template attributes
    Object.assign(window, {
        toggleTheme, toggleDrawer, toggleChat, switchTab,
        openStaplesModal, closeStaplesModal, deleteStaple, addStaple,
        toggleEmailValue, checkAllItems, deleteCheckedItems,
        copyShoppingList, printShoppingList, quickAddShoppingItem,
        loadSwapRecommendations, onSwapSelected, showLoading, submitPlanForm,
    });
})();
