// ==UserScript==
// @name         gmail localhost free claude thingy
// @namespace    http://tampermonkey.net/
// @description  so basically paste this script into tampermonkey and go to gmail.com and then once you are on gmail.com hit ctrl+shift+r wich should hard refresh the page but do this only if the script dosent load
// @version      9.1
// @match        https://mail.google.com/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      127.0.0.1
// ==/UserScript==

(function() {
    'use strict';

    console.log('⚡ Ace Code Bridge: Script loaded v9.1');

    let lastSentCode = "";
    let isProcessing = false;

    // --- Create UI Elements ---
    // Gmail enforces Trusted Types (`require-trusted-types-for 'script'`), so
    // `element.innerHTML = '...'` throws "This document requires 'TrustedHTML'
    // assignment" and aborts initUI() before the panel is ever attached. Every
    // node here is therefore built with createElement/textContent instead.
    function makeEl(tag, cssText, text) {
        const node = document.createElement(tag);
        if (cssText) node.style.cssText = cssText;
        if (text != null) node.textContent = text;
        return node;
    }

    function makeRow(labelText) {
        const row = makeEl('div', 'display:flex; justify-content:space-between; align-items:center;');
        row.appendChild(makeEl('span', null, labelText));
        return row;
    }

    function initUI() {
        // document.body is null at document-start; nothing to attach to yet.
        if (!document.body) return;

        const existingUI = document.getElementById('ace-bridge-ui');
        if (existingUI) {
            if (document.body.contains(existingUI)) return;
            existingUI.remove();
        }

        const container = makeEl('div', `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 999999;
            width: 280px;
            background: #1e1e2e;
            color: #cdd6f4;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 13px;
            border-radius: 10px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
            border: 1px solid #45475a;
            overflow: hidden;
            user-select: none;
        `);
        container.id = 'ace-bridge-ui';

        const header = makeEl('div', `
            background: #11111b;
            padding: 8px 12px;
            font-weight: bold;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: move;
            border-bottom: 1px solid #313244;
        `);
        header.id = 'ace-bridge-header';
        header.appendChild(makeEl('span', null, '⚡ Ace Code Bridge'));

        const headerButtons = makeEl('div');
        const minBtn = makeEl('button', 'background:none; border:none; color:#a6adc8; cursor:pointer; font-size:14px; padding:0 4px;', '−');
        minBtn.id = 'ace-ui-min';
        minBtn.type = 'button';
        headerButtons.appendChild(minBtn);
        header.appendChild(headerButtons);

        const content = makeEl('div', 'padding: 12px; display: flex; flex-direction: column; gap: 8px;');
        content.id = 'ace-ui-content';

        const statusRow = makeRow('Status:');
        const statusEl = makeEl('span', 'color: #f9e2af; font-weight:600;', 'Listening...');
        statusEl.id = 'ace-status';
        statusRow.appendChild(statusEl);

        const codeRow = makeRow('Last Code:');
        const codeEl = makeEl('span', 'font-family:monospace; font-size:14px; color:#89b4fa; font-weight:bold;', '------');
        codeEl.id = 'ace-last-code';
        codeRow.appendChild(codeEl);

        const toggleRow = makeEl('div', `
            display:flex;
            justify-content:space-between;
            align-items:center;
            background:#11111b;
            padding:6px 8px;
            border-radius:6px;
            margin-top:2px;
        `);
        toggleRow.appendChild(makeEl('span', 'font-size:12px;', 'Inbox Refresh (2s):'));

        const toggleLabel = makeEl('label', 'position: relative; display: inline-block; width: 34px; height: 18px; cursor: pointer;');
        const toggleCheckbox = makeEl('input', 'opacity: 0; width: 0; height: 0;');
        toggleCheckbox.type = 'checkbox';
        toggleCheckbox.id = 'ace-refresh-toggle';
        toggleCheckbox.checked = isInboxRefreshEnabled;

        const slider = makeEl('span', 'position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; transition:.3s; border-radius:18px;');
        slider.id = 'ace-toggle-slider';
        const knob = makeEl('span', 'position:absolute; height:14px; width:14px; bottom:2px; background-color:#11111b; transition:.3s; border-radius:50%;');
        knob.id = 'ace-toggle-knob';

        // Keep the visuals in sync with the real state if the panel is rebuilt.
        slider.style.backgroundColor = isInboxRefreshEnabled ? '#a6e3a1' : '#45475a';
        knob.style.left = isInboxRefreshEnabled ? '18px' : '2px';

        toggleLabel.append(toggleCheckbox, slider, knob);
        toggleRow.appendChild(toggleLabel);

        const deleteBtn = makeEl('button', `
            background: #f38ba8;
            color: #11111b;
            border: none;
            padding: 6px 10px;
            font-weight: bold;
            border-radius: 6px;
            cursor: pointer;
            margin-top: 4px;
        `, '🗑️ Delete ACE Emails');
        deleteBtn.id = 'ace-delete-btn';
        deleteBtn.type = 'button';

        content.append(statusRow, codeRow, toggleRow, deleteBtn);
        container.appendChild(header);
        container.appendChild(content);

        const targetParent = document.body || document.documentElement;
        targetParent.appendChild(container);
        setupUIEventListeners();
    }

    function safeInitUI() {
        try {
            initUI();
        } catch (err) {
            console.error('ACE Bridge: initUI failed:', err);
        }
    }

    // Recreated panels must not stack duplicate drag listeners on document.
    let dragBound = false;

    function setupUIEventListeners() {
        const minBtn = document.getElementById('ace-ui-min');
        const deleteBtnUI = document.getElementById('ace-delete-btn');
        const container = document.getElementById('ace-bridge-ui');
        const content = document.getElementById('ace-ui-content');
        const header = document.getElementById('ace-bridge-header');
        const toggleCheckbox = document.getElementById('ace-refresh-toggle');
        const slider = document.getElementById('ace-toggle-slider');
        const knob = document.getElementById('ace-toggle-knob');

        if (!container || !header || !minBtn) return;

        let isMinimized = false;
        minBtn.addEventListener('click', () => {
            isMinimized = !isMinimized;
            content.style.display = isMinimized ? 'none' : 'flex';
            minBtn.textContent = isMinimized ? '+' : '−';
        });

        if (toggleCheckbox) {
            toggleCheckbox.addEventListener('change', () => {
                isInboxRefreshEnabled = toggleCheckbox.checked;
                if (isInboxRefreshEnabled) {
                    slider.style.backgroundColor = '#a6e3a1';
                    knob.style.left = '18px';
                    startInboxRefresh();
                    updateStatus('Inbox Refresh On', '#a6e3a1');
                    setTimeout(() => { if(!isProcessing) updateStatus('Listening...', '#cdd6f4'); }, 1500);
                } else {
                    slider.style.backgroundColor = '#45475a';
                    knob.style.left = '2px';
                    stopInboxRefresh();
                    updateStatus('Refresh Paused', '#f9e2af');
                    setTimeout(() => { if(!isProcessing) updateStatus('Listening...', '#cdd6f4'); }, 1500);
                }
            });
        }

        if (!dragBound) {
            dragBound = true;
            let isDragging = false, offsetX = 0, offsetY = 0;

            header.addEventListener('mousedown', (e) => {
                if (e.target === minBtn) return;
                isDragging = true;
                offsetX = e.clientX - container.getBoundingClientRect().left;
                offsetY = e.clientY - container.getBoundingClientRect().top;
            });
            document.addEventListener('mousemove', (e) => {
                if (!isDragging) return;
                container.style.left = `${e.clientX - offsetX}px`;
                container.style.top = `${e.clientY - offsetY}px`;
                container.style.right = 'auto';
            });
            document.addEventListener('mouseup', () => { isDragging = false; });
        }

        if (deleteBtnUI) {
            deleteBtnUI.addEventListener('click', () => {
                // Single shared entry point - the auto-delete path calls
                // deleteAceEmails() too, so both behave identically.
                deleteAceEmails();
            });
        }
    }

    function collectAceRows() {
        return Array.from(document.querySelectorAll('tr.zA'))
            .filter(row => isAceEmail(row.innerText));
    }

    function deleteAceEmails(explicitRows, onComplete) {
        const targetRows = (explicitRows && explicitRows.length)
            ? explicitRows
            : collectAceRows();

        if (!targetRows.length) {
            updateStatus('No ACE Emails Found', '#f9e2af');
            setTimeout(() => { if (!isProcessing) updateStatus('Listening...', '#cdd6f4'); }, 2000);
            if (onComplete) onComplete(false);
            return;
        }

        updateStatus('Cleaning ACE...', '#f9e2af');
        processDeletion(targetRows, (confirmed) => {
            setTimeout(() => { if (!isProcessing) updateStatus('Listening...', '#cdd6f4'); }, 2000);
            if (onComplete) onComplete(confirmed);
        });
    }

    function delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function waitFor(predicate, timeoutMs) {
        return new Promise(resolve => {
            const started = Date.now();
            const tick = () => {
                if (predicate()) {
                    resolve(true);
                    return;
                }
                if (Date.now() - started >= timeoutMs) {
                    resolve(false);
                    return;
                }
                setTimeout(tick, 150);
            };
            tick();
        });
    }

    // Promise wrapper around the button's own entry point. deleteAceEmails()
    // with no rows is exactly what the UI button does, so the auto path cannot
    // drift away from the code path that is known to work.
    function deleteAceEmailsOnce(timeoutMs) {
        return new Promise(resolve => {
            let settled = false;
            const done = (confirmed) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                resolve(!!confirmed);
            };
            // Safety net: a stalled run must not wedge isProcessing forever.
            const timer = setTimeout(() => done(false), timeoutMs);

            try {
                deleteAceEmails(undefined, done);
            } catch (err) {
                console.error('ACE Bridge: deleteAceEmails threw:', err);
                done(false);
            }
        });
    }

    // Auto-delete. The old version hand-picked one row by matching the 6-digit
    // code in innerText and passed it as an explicit row list. Two problems:
    //   1) it ran ~400ms after the bridge replied - before the list had
    //      settled - so the toolbar Delete never enabled and it gave up;
    //   2) the row lookup is a race against Gmail re-rendering the list.
    // The UI button always works because by then the page is settled. So: wait
    // for the list, then call the button's exact code path, and retry it.
    async function autoDeleteSentCode(code) {
        updateStatus('Auto-Deleting...', '#f38ba8');

        const rowsReady = await waitFor(() => collectAceRows().length > 0, 5000);
        if (!rowsReady) {
            console.warn(`ACE Bridge: no ACE row visible yet for code ${code}`);
            updateStatus('No ACE Row Yet', '#f9e2af');
        }

        // Let Gmail finish arming its selection model before the first attempt.
        await delay(500);

        for (let attempt = 1; attempt <= 3; attempt++) {
            console.log(`⚡ Ace Code Bridge: auto-delete attempt ${attempt}/3`);
            if (await deleteAceEmailsOnce(25000)) {
                console.log('⚡ Ace Code Bridge: auto-delete confirmed');
                return true;
            }
            if (attempt < 3) {
                updateStatus(`Retry Delete (${attempt}/3)`, '#f9e2af');
                await delay(1200);
            }
        }

        console.warn('ACE Bridge: auto-delete gave up after 3 attempts');
        updateStatus('Auto-Delete Failed', '#f38ba8');
        return false;
    }

    // --- Inbox refresh ---
    // Clicking Gmail's sidebar "Inbox" entry re-runs the list fetch, which is
    // enough for new mail to show up. No page reload, so no beforeunload
    // suppression, no lost scroll/selection state and no cache-busted URL.
    const INBOX_REFRESH_MS = 2000;
    let inboxRefreshInterval = null;
    let isInboxRefreshEnabled = true; // Starts on by default

    // Gmail ships more than one thing that points at #inbox (the sidebar item
    // and the "Back to Inbox" arrow in a thread toolbar). Score them so the
    // sidebar entry wins instead of whichever happens to be first in the DOM.
    function findInboxNavLink() {
        const scored = [];

        document.querySelectorAll('a[href$="#inbox"]').forEach(link => {
            if (!link.isConnected) return;

            const rect = link.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;

            let score = 0;
            if (link.closest('div[role="navigation"]')) score += 10; // sidebar
            if (rect.left < 240) score += 5;                          // left edge of the window
            const label = (link.getAttribute('aria-label') || link.innerText || '').trim();
            if (/^inbox\b/i.test(label)) score += 3;
            if (/back to inbox/i.test(label)) score -= 20;             // thread toolbar arrow

            scored.push({ link, score });
        });

        scored.sort((a, b) => b.score - a.score);
        return scored.length ? scored[0].link : null;
    }

    // Gmail mutates the DOM constantly, so warn once per streak instead of
    // flooding the console (and the status line) every 2 seconds.
    let inboxLinkWarned = false;
    let inboxPausedByDialog = false;

    function hasVisibleDialog() {
        return Array.from(document.querySelectorAll('[role="dialog"]')).some(el => {
            if (el.getAttribute('aria-hidden') === 'true') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        });
    }

    function canRefreshInbox() {
        if (isProcessing) return false;
        if (!document.body) return false;

        // Don't pull the user out of a compose window / settings dialog. Check
        // visibility, because Gmail keeps hidden dialog templates in the DOM.
        if (hasVisibleDialog()) {
            if (!inboxPausedByDialog) {
                inboxPausedByDialog = true;
                console.log('ACE Bridge: inbox refresh paused (dialog open)');
            }
            return false;
        }
        inboxPausedByDialog = false;
        return true;
    }

    function refreshInboxByClicking() {
        if (!canRefreshInbox()) return false;

        const link = findInboxNavLink();
        if (!link) {
            if (!inboxLinkWarned) {
                inboxLinkWarned = true;
                console.warn('ACE Bridge: could not find the Inbox nav link');
                updateStatus('Inbox Link Missing', '#f9e2af');
            }
            return false;
        }
        inboxLinkWarned = false;

        // A real anchor click is enough here - Gmail's nav handler listens for
        // the click event itself, no pointerdown/mouseup dance required.
        try {
            link.click();
        } catch (err) {
            console.warn('ACE Bridge: nav click failed, trying pointer chain', err);
            fireRealClick(link);
        }

        return true;
    }

    function startInboxRefresh() {
        stopInboxRefresh();
        if (!isInboxRefreshEnabled) return;

        inboxRefreshInterval = setInterval(() => {
            refreshInboxByClicking();
        }, INBOX_REFRESH_MS);
    }

    function stopInboxRefresh() {
        if (inboxRefreshInterval) {
            clearInterval(inboxRefreshInterval);
            inboxRefreshInterval = null;
        }
    }

    function updateStatus(text, color) {
        const statusEl = document.getElementById('ace-status');
        if (statusEl) {
            statusEl.textContent = text;
            statusEl.style.color = color;
        }
    }

    function isAceEmail(rowText) {
        const lower = rowText.toLowerCase();
        return lower.includes("ace data cloud") || lower.includes("acidic");
    }

    // Gmail ships several "Delete" controls (list toolbar, message toolbar,
    // overflow menus, hidden templates). Score them instead of trusting order.
    function findTrashButton() {
        const nodes = document.querySelectorAll(
            '[role="button"][aria-label="Delete"], [role="button"][data-tooltip="Delete"], ' +
            'button[aria-label="Delete"], [role="button"][data-tooltip^="Delete"]'
        );

        const scored = [];

        nodes.forEach(button => {
            if (button.closest('#ace-bridge-ui')) return; // never click our own UI

            if (!button.isConnected) return;
            if (button.getAttribute('aria-disabled') === 'true') return;

            const rect = button.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return;

            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const inViewport = cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight;

            let uncovered = false;
            if (inViewport) {
                const hit = document.elementFromPoint(cx, cy);
                uncovered = !!hit && (hit === button || button.contains(hit));
            }

            let score = 0;
            if (button.closest('[role="toolbar"]')) score += 10; // list toolbar wins
            if (inViewport) score += 5;
            if (uncovered) score += 5;                            // not hidden behind an overlay
            if (/delete/i.test(button.getAttribute('data-tooltip') || '')) score += 3;

            scored.push({ button, score, rect });
        });

        scored.sort((a, b) => b.score - a.score);

        return scored.length ? scored[0].button : null;
    }

    // Gmail keeps the toolbar Delete disabled until its internal selection
    // model catches up. Poll instead of guessing with a fixed delay.
    function waitForEnabledTrashButton(timeoutMs) {
        return new Promise(resolve => {
            const started = Date.now();
            const attempt = () => {
                const button = findTrashButton();
                if (button) {
                    resolve(button);
                    return;
                }
                if (Date.now() - started >= timeoutMs) {
                    resolve(null);
                    return;
                }
                setTimeout(attempt, 120);
            };
            attempt();
        });
    }

    function hoverElement(el) {
        if (!el) return;

        const rect = el.getBoundingClientRect();
        const opts = {
            bubbles: true,
            cancelable: true,
            detail: 0,
            button: 0,
            buttons: 0,
            clientX: rect.left + Math.min(rect.width / 2, 40),
            clientY: rect.top + rect.height / 2
        };
        const pointer = {
            ...opts,
            pointerId: 1,
            pointerType: 'mouse',
            isPrimary: true
        };

        el.dispatchEvent(new PointerEvent('pointerover', pointer));
        el.dispatchEvent(new MouseEvent('mouseover', opts));
        el.dispatchEvent(new MouseEvent('mousemove', opts));
    }

    // Fallback that skips the toolbar entirely: Gmail renders per-row action
    // icons (including Delete) on hover. Reveal the row, then grab its button.
    function findRowDeleteButton(row) {
        return new Promise(resolve => {
            const pick = () => {
                const el = row.querySelector(
                    '[role="button"][aria-label="Delete"], [role="button"][data-tooltip="Delete"]'
                );
                return el && isClickable(el) ? el : null;
            };

            const immediate = pick();
            if (immediate) {
                resolve(immediate);
                return;
            }

            // Row actions are hidden until the row is hovered.
            hoverElement(row);

            setTimeout(() => resolve(pick()), 250);
        });
    }

    // Gmail toolbar buttons are div[role="button"]; some only arm their action
    // from a real pointer/mouse down-up cycle, so a bare .click() can be ignored.
    // Also, dispatching a synthetic KeyboardEvent never triggers a native
    // <button>'s default action, so Enter is only a fallback for role=button.

    function isClickable(el) {
        if (!el || !el.isConnected) return false;
        if (el.disabled) return false;
        if (el.getAttribute('aria-disabled') === 'true') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function resolveButton(el) {
        if (!el) return null;
        return el.closest('[role="button"], button, a') || el;
    }

    function fireRealClick(el) {
        const target = resolveButton(el);
        if (!isClickable(target)) return false;

        const rect = target.getBoundingClientRect();
        const opts = {
            bubbles: true,
            cancelable: true,
            detail: 1,
            button: 0,
            buttons: 0,
            clientX: rect.left + 2,
            clientY: rect.top + 2,
            screenX: rect.left + 2,
            screenY: rect.top + 2
        };
        const pointer = {
            ...opts,
            pointerId: 1,
            pointerType: 'mouse',
            isPrimary: true
        };

        // Gmail's Closure widgets rebuild a human hover -> press -> release.
        // A plain .click() (or a lone click event) is silently ignored.
        target.dispatchEvent(new PointerEvent('pointerover', pointer));
        target.dispatchEvent(new MouseEvent('mouseover', opts));
        target.dispatchEvent(new MouseEvent('mouseenter', opts));
        target.dispatchEvent(new MouseEvent('mousemove', opts));

        target.dispatchEvent(new PointerEvent('pointerdown', { ...pointer, buttons: 1 }));
        target.dispatchEvent(new MouseEvent('mousedown', { ...opts, buttons: 1 }));
        target.dispatchEvent(new PointerEvent('pointerup', { ...pointer, buttons: 0 }));
        target.dispatchEvent(new MouseEvent('mouseup', { ...opts, buttons: 0 }));
        target.dispatchEvent(new MouseEvent('click', opts));

        return true;
    }

    function fireEnterKey(el) {
        const target = resolveButton(el);
        if (!isClickable(target)) return false;

        target.focus();
        const keyInit = {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            charCode: 0,
            bubbles: true,
            cancelable: true
        };

        target.dispatchEvent(new KeyboardEvent('keydown', keyInit));
        target.dispatchEvent(new KeyboardEvent('keypress', keyInit));
        target.dispatchEvent(new KeyboardEvent('keyup', keyInit));

        // Native <button> won't activate from an untrusted Enter, so click it too.
        if (target.tagName === 'BUTTON') target.click();
        return true;
    }

    function fireSpaceKey(el) {
        const target = resolveButton(el);
        if (!isClickable(target)) return false;

        target.focus();
        const keyInit = {
            key: ' ',
            code: 'Space',
            keyCode: 32,
            which: 32,
            charCode: 32,
            bubbles: true,
            cancelable: true
        };

        target.dispatchEvent(new KeyboardEvent('keydown', keyInit));
        target.dispatchEvent(new KeyboardEvent('keyup', keyInit));

        if (target.tagName === 'BUTTON') target.click();
        return true;
    }

    function getVisibleAlerts() {
        return Array.from(document.querySelectorAll('[role="alert"]'))
            .map(el => (el.innerText || '').trim())
            .filter(Boolean);
    }

    function checkDeletionState(targetRows, baselineAlerts) {
        const allGone = targetRows.every(row => !document.body.contains(row) || row.offsetParent === null);

        // Only count a snackbar that appeared AFTER we started, otherwise a
        // stale "moved to trash" toast would look like a successful delete.
        const newAlert = getVisibleAlerts().some(text =>
            !baselineAlerts.includes(text) && /trash|moved|conversation/i.test(text)
        );

        return allGone || newAlert;
    }

    function waitForDeletion(targetRows, timeoutMs, baselineAlerts) {
        return new Promise(resolve => {
            const started = Date.now();
            const interval = setInterval(() => {
                if (checkDeletionState(targetRows, baselineAlerts)) {
                    clearInterval(interval);
                    resolve(true);
                } else if (Date.now() - started >= timeoutMs) {
                    clearInterval(interval);
                    resolve(false);
                }
            }, 100);
        });
    }

    async function processDeletion(targetRows, onComplete) {
        const wasProcessing = isProcessing;
        isProcessing = true; // keep the inbox refresh from clicking mid-delete

        try {
            if (!targetRows || targetRows.length === 0) {
                updateStatus('No Target Rows', '#f9e2af');
                if (onComplete) onComplete(false);
                return;
            }

            let successfullySelected = 0;

            for (const row of targetRows) {
                if (!document.body.contains(row)) continue;

                const checkbox = row.querySelector('div[role="checkbox"]');
                if (!checkbox) continue;

                // Auto-delete runs without the mouse anywhere near the list;
                // Gmail only activates row controls while the row is hovered.
                hoverElement(row);
                await new Promise(resolve => setTimeout(resolve, 60));

                if (checkbox.getAttribute('aria-checked') !== 'true') {
                    // Gmail checkboxes are Closure widgets too - use the real
                    // event chain, then fall back to .click() if that missed.
                    fireRealClick(checkbox);
                    if (checkbox.getAttribute('aria-checked') !== 'true') {
                        checkbox.click();
                    }
                }

                if (checkbox.getAttribute('aria-checked') === 'true') {
                    successfullySelected++;
                }
            }

            if (successfullySelected === 0) {
                updateStatus('Selection Failed', '#f9e2af');
                if (onComplete) onComplete(false);
                return;
            }

            // Wait for Gmail's selection model to enable the toolbar button.
            let deleteButton = await waitForEnabledTrashButton(2500);

            // If the toolbar never enabled, the checkbox click may not have
            // registered internally - re-click and wait once more.
            if (!deleteButton) {
                targetRows.forEach(row => {
                    const checkbox = row.querySelector('div[role="checkbox"]');
                    if (checkbox && checkbox.getAttribute('aria-checked') !== 'true') {
                        fireRealClick(checkbox);
                    }
                });
                deleteButton = await waitForEnabledTrashButton(2500);
            }

            if (!deleteButton) {
                updateStatus('Toolbar Locked / Missing', '#f9e2af');
                if (onComplete) onComplete(false);
                return;
            }

            console.log('Found delete button:', deleteButton);
            console.log('Delete button markup:', (deleteButton.outerHTML || '').slice(0, 400));
            updateStatus('Deleting...', '#f38ba8');

            const baselineAlerts = getVisibleAlerts();

            // 1) The proven Gmail path: full pointer/mouse sequence.
            // 2) Keyboard fallbacks (some widgets listen for Enter/Space).
            // 3) Re-find + click, in case the toolbar re-rendered after focus.
            const strategies = [
                { name: 'real pointer/mouse sequence', run: () => fireRealClick(deleteButton) },
                { name: 'Enter key', run: () => fireEnterKey(deleteButton) },
                { name: 'Space key', run: () => fireSpaceKey(deleteButton) },
                {
                    name: 're-find + real click',
                    run: () => {
                        deleteButton = findTrashButton() || deleteButton;
                        return fireRealClick(deleteButton);
                    }
                }
            ];

            for (const strategy of strategies) {
                if (checkDeletionState(targetRows, baselineAlerts)) break;

                let fired = false;
                try {
                    fired = strategy.run();
                } catch (err) {
                    console.error(`ACE Bridge: ${strategy.name} threw:`, err);
                }
                console.log(`⚡ Delete attempt via ${strategy.name}:`, fired);

                // Give Gmail a moment, but never block the loop on a timer that
                // a background tab may throttle.
                if (await waitForDeletion(targetRows, 1000, baselineAlerts)) break;
            }

            // Last resort: use each row's own hover Delete icon, no toolbar.
            if (!checkDeletionState(targetRows, baselineAlerts)) {
                for (const row of targetRows) {
                    if (checkDeletionState(targetRows, baselineAlerts)) break;
                    if (!document.body.contains(row)) continue;

                    const rowDelete = await findRowDeleteButton(row);
                    console.log('⚡ Row-level delete button:', rowDelete);

                    if (rowDelete) {
                        try {
                            fireRealClick(rowDelete);
                        } catch (err) {
                            console.error('ACE Bridge: row delete threw:', err);
                        }
                        if (await waitForDeletion(targetRows, 1000, baselineAlerts)) break;
                    }
                }
            }

            const confirmed = checkDeletionState(targetRows, baselineAlerts);
            if (confirmed) {
                updateStatus('Moved to Trash!', '#a6e3a1');
            } else {
                updateStatus('Action Unconfirmed', '#f9e2af');
                console.warn('ACE Bridge: delete was not confirmed. Button still present:', document.body.contains(deleteButton));
            }
            if (onComplete) onComplete(confirmed);
        } catch (err) {
            console.error('ACE Bridge: processDeletion crashed:', err);
            updateStatus('Delete Error (see console)', '#f38ba8');
            if (onComplete) onComplete(false);
        } finally {
            isProcessing = wasProcessing;
        }
    }

    function startUI() {
        // document-start: body usually doesn't exist yet, but the injection can
        // also happen after DOMContentLoaded (SPA navigation, cached restore),
        // so check readyState instead of always waiting for the event.
        safeInitUI();
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', safeInitUI, { once: true });
        }
        window.addEventListener('load', safeInitUI, { once: true });
    }

    startUI();

    window.addEventListener('pageshow', (event) => {
        safeInitUI();
        startInboxRefresh();
    });

    // Gmail mutates the DOM constantly. The original observer ran on every
    // single mutation, which alone can make the tab feel dead. Coalesce it.
    let uiCheckScheduled = false;
    const uiObserver = new MutationObserver(() => {
        if (uiCheckScheduled) return;
        uiCheckScheduled = true;
        setTimeout(() => {
            uiCheckScheduled = false;
            safeInitUI();
        }, 250);
    });

    uiObserver.observe(document.documentElement || document, {
        childList: true,
        subtree: true
    });

    startInboxRefresh();

    // --- Send the code to the local Python bridge ---
    // One shot: whichever callback fires first wins, so isProcessing can never
    // get stuck true (which would silently kill the loop and the UI).
    function sendCodeToBridge(code) {
        isProcessing = true;
        lastSentCode = code;

        const codeEl = document.getElementById('ace-last-code');
        if (codeEl) codeEl.textContent = code;
        updateStatus('Found Code!', '#a6e3a1');

        let settled = false;
        let watchdog = null;

        const release = (ok) => {
            setTimeout(() => {
                isProcessing = false;
                if (!ok) lastSentCode = ""; // allow a retry for the same code
                updateStatus('Listening...', '#cdd6f4');
            }, ok ? 1500 : 1000);
        };

        const finish = (ok, statusText, statusColor, thenDelete) => {
            if (settled) return;
            settled = true;
            if (watchdog) clearTimeout(watchdog);
            updateStatus(statusText, statusColor);

            if (!thenDelete) {
                release(ok);
                return;
            }

            // The bridge has the code, so run the exact same delete the button
            // triggers - no hand-picked rows, retried until it sticks.
            setTimeout(() => {
                autoDeleteSentCode(code).finally(() => {
                    setTimeout(() => {
                        isProcessing = false;
                        updateStatus('Listening...', '#cdd6f4');
                    }, 1500);
                });
            }, 400);
        };

        // Hard cap: never let a hung request freeze the bridge forever.
        watchdog = setTimeout(() => {
            console.warn('ACE Bridge: request timed out');
            finish(false, 'Connection Timed Out', '#f38ba8', false);
        }, 20000);

        if (typeof GM_xmlhttpRequest !== 'function') {
            console.error('ACE Bridge: GM_xmlhttpRequest missing - check the @grant line and that the script is enabled.');
            finish(false, 'GM Grant Missing', '#f38ba8', false);
            return;
        }

        try {
            GM_xmlhttpRequest({
                method: "POST",
                url: "http://localhost:8080/",
                headers: { "Content-Type": "application/json" },
                data: JSON.stringify({ code: code }),
                timeout: 8000,
                onload: function(response) {
                    // Any completed response means the bridge got the code, so
                    // clean up regardless. A bare 4xx/5xx from a local server
                    // still means it was delivered - only a network-level
                    // failure should stop the delete.
                    if (response.status >= 400) {
                        console.warn('ACE Bridge: bridge replied', response.status, '- deleting anyway');
                    }
                    finish(true, 'Sent to Python', '#89b4fa', true);
                },
                onerror: function(err) {
                    console.error('ACE Bridge: bridge unreachable', err);
                    finish(false, 'Connection Failed', '#f38ba8', false);
                },
                ontimeout: function() {
                    console.warn('ACE Bridge: bridge request timed out');
                    finish(false, 'Connection Timed Out', '#f38ba8', false);
                }
            });
        } catch (err) {
            console.error('ACE Bridge: GM_xmlhttpRequest threw:', err);
            finish(false, 'Connection Failed', '#f38ba8', false);
        }
    }

    // Core Loop
    setInterval(() => {
        if (isProcessing) return;

        const rows = document.querySelectorAll('tr.zA');
        for (const row of rows) {
            const rowText = row.innerText;

            if (isAceEmail(rowText)) {
                const match = rowText.match(/\b(\d{6})\b/);
                if (match && match[1]) {
                    const code = match[1];
                    if (code !== lastSentCode) {
                        sendCodeToBridge(code);
                        break;
                    }
                }
            }
        }
    }, 1000);
})();
