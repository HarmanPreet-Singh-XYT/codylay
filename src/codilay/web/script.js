// ── Theme Management ─────────────────────────────────────────────────────────
function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);

    // Update hljs stylesheet
    document.getElementById('hljs-theme').href = `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github${newTheme === 'light' ? '' : '-dark'}.min.css`;

    updateThemeIcon();
}

function updateThemeIcon() {
    const theme = document.documentElement.getAttribute('data-theme');
    const iconEl = document.getElementById('theme-icon');
    if (iconEl) {
        // Switch between sun/moon icon based on theme
        iconEl.setAttribute('data-lucide', theme === 'light' ? 'moon' : 'sun');
        if (window.lucide) lucide.createIcons({ nameAttr: 'data-lucide', attrs: { class: "lucide" } });
    }
}

// ── Icons Helper ─────────────────────────────────────────────────────────────
function updateIcons() {
    if (window.lucide) {
        lucide.createIcons();
    }
}

// ── State ────────────────────────────────────────────────────────────────────
let sections = [];
let links = {};
let currentView = 'document';
let currentTab = 'sections';
let chatOpen = true;

// ── Init ─────────────────────────────────────────────────────────────────────
async function init() {
    updateThemeIcon(); // Setup correct icon on load
    chatNewConversation();

    try {
        const [sectionsRes, linksRes, statsRes] = await Promise.all([
            fetch('/api/sections').then(r => r.json()).catch(() => ({ sections: [] })),
            fetch('/api/links').then(r => r.json()).catch(() => ({})),
            fetch('/api/stats').then(r => r.json()).catch(() => ({ project: 'Project Name', files_processed: 0, sections: 0, closed_wires: 0 })),
        ]);

        sections = sectionsRes.sections || [];
        links = linksRes || {};

        document.getElementById('project-name').textContent = statsRes.project || 'Project Data';
        document.title = `CodiLay - ${statsRes.project || 'Dashboard'}`;

        renderStats(statsRes);
        renderSidebar();
        renderDocument();
        updateIcons();
    } catch (err) {
        document.getElementById('main-content').innerHTML =
            `<div class="loading" style="color:var(--red)"><i data-lucide="alert-circle" style="margin-right:8px;"></i> Failed to load: ${err.message}</div>`;
        updateIcons();
    }
}

// ── Stats ────────────────────────────────────────────────────────────────────
function renderStats(stats) {
    const el = document.getElementById('sidebar-stats');
    el.innerHTML = `
    <div class="stat"><strong>${stats.files_processed || 0}</strong> files</div>
    <div class="stat"><strong>${stats.sections || 0}</strong> sections</div>
    <div class="stat"><strong>${stats.closed_wires || 0}</strong> wires</div>
    ${stats.last_commit ? `<div class="stat">@ <strong>${stats.last_commit}</strong></div>` : ''}
    `;
}

// ── Sidebar ──────────────────────────────────────────────────────────────────
function renderSidebar() {
    const nav = document.getElementById('sidebar-nav');
    const search = document.getElementById('section-search').value.toLowerCase();

    if (currentTab === 'sections') {
        const filtered = sections.filter(s =>
            !search || s.title.toLowerCase().includes(search) ||
            (s.file && s.file.toLowerCase().includes(search)) ||
            (s.tags && s.tags.some(t => t.toLowerCase().includes(search)))
        );

        nav.innerHTML = filtered.map(s => `
        <div class="nav-item" data-id="${s.id}" onclick="scrollToSection('${s.id}')">
        ${escHtml(s.title)}
        ${s.file ? `<span class="file-ref">${escHtml(s.file)}</span>` : ''}
        </div>
    `).join('');
    } else {
        const files = new Map();
        sections.forEach(s => {
            if (s.file) {
                if (!files.has(s.file)) files.set(s.file, []);
                files.get(s.file).push(s);
            }
        });

        const entries = [...files.entries()].filter(([f]) =>
            !search || f.toLowerCase().includes(search)
        );

        nav.innerHTML = entries.map(([file, secs]) => `
        <div class="nav-item" onclick="openFile('${escAttr(file)}')">
        ${escHtml(file)}
        <span class="file-ref">${secs.length} section${secs.length > 1 ? 's' : ''}</span>
        </div>
    `).join('');
    }
}

// ── Main views ───────────────────────────────────────────────────────────────
function renderDocument() {
    const container = document.getElementById('main-content');

    if (sections.length === 0) {
        container.innerHTML = '<div class="loading">No sections found.</div>';
        return;
    }

    const html = sections.map(s => `
    <div class="section-card" id="section-${s.id}">
        <div class="doc-content">
        ${renderMarkdown(`## ${s.title}\n${s.file ? `> File: \`${s.file}\`\n\n` : ''}${s.content || ''}`)}
        </div>
    </div>
    `).join('');

    container.innerHTML = html;

    container.querySelectorAll('code').forEach(el => {
        const text = el.textContent;
        if (text.includes('/') && !text.includes(' ') && text.length < 100) {
            el.style.cursor = 'pointer';
            el.style.textDecoration = 'underline';
            el.addEventListener('click', () => openFile(text));
        }
    });

    updateIcons();
}

function renderGraph() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div class="graph-container" id="graph-container" style="position:relative; width: 100%; height: 100%; min-height: 500px;">
        <div class="graph-filter-bar" id="graph-filter-bar">
        <strong style="color:var(--text); font-size:12px;">Filters</strong>
        <label>Wire Type</label>
        <select id="gf-wire-type"><option value="">All</option></select>
        <label>Layer</label>
        <select id="gf-layer"><option value="">All</option></select>
        <label>Module</label>
        <select id="gf-module"><option value="">All</option></select>
        <label>Min Connections</label>
        <input id="gf-min-conn" type="number" min="0" value="0"
            style="padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px;width:60px;">
        <button class="tool-btn primary" onclick="applyGraphFilters()" style="margin-top:4px;justify-content:center;">Apply</button>
        <button class="tool-btn" onclick="resetGraphFilters()" style="justify-content:center;">Reset</button>
        </div>
        <div style="position: absolute; top: 12px; right: 12px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px; font-size: 11px;">
        <div style="display: flex; align-items: center; gap: 6px; margin: 4px 0; color: var(--text-muted);">
            <div style="width: 8px; height: 8px; border-radius: 50%; background: var(--accent);"></div> Source file
        </div>
        <div style="display: flex; align-items: center; gap: 6px; margin: 4px 0; color: var(--text-muted);">
            <div style="width: 8px; height: 8px; border-radius: 50%; background: var(--green);"></div> Resolved (closed)
        </div>
        <div style="display: flex; align-items: center; gap: 6px; margin: 4px 0; color: var(--text-muted);">
            <div style="width: 8px; height: 8px; border-radius: 50%; background: var(--orange);"></div> Open wire
        </div>
        <div id="gf-stats" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);color:var(--text-muted);display:none;"></div>
        </div>
    </div>
    `;

    loadGraphFilters();
    buildGraph();
}

async function loadGraphFilters() {
    try {
        const res = await fetch('/api/graph/filters');
        if (!res.ok) return;
        const data = await res.json();
        const wireTypeSel = document.getElementById('gf-wire-type');
        const layerSel = document.getElementById('gf-layer');
        const moduleSel = document.getElementById('gf-module');
        (data.wire_types || []).forEach(t => {
            wireTypeSel.innerHTML += `<option value="${escHtml(t)}">${escHtml(t)}</option>`;
        });
        (data.layers || []).forEach(l => {
            layerSel.innerHTML += `<option value="${escHtml(l)}">${escHtml(l)}</option>`;
        });
        (data.modules || []).forEach(m => {
            moduleSel.innerHTML += `<option value="${escHtml(m)}">${escHtml(m)}</option>`;
        });
    } catch (e) { /* filters unavailable, no-op */ }
}

async function applyGraphFilters() {
    const wireType = document.getElementById('gf-wire-type').value;
    const layer = document.getElementById('gf-layer').value;
    const module = document.getElementById('gf-module').value;
    const minConn = parseInt(document.getElementById('gf-min-conn').value) || 0;

    const body = {
        wire_types: wireType ? [wireType] : null,
        layers: layer ? [layer] : null,
        modules: module ? [module] : null,
        min_connections: minConn,
        direction: 'both',
    };

    try {
        const res = await fetch('/api/graph/filter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) return;
        const data = await res.json();

        // Rebuild graph with filtered data
        const graphContainer = document.getElementById('graph-container');
        const svg = graphContainer.querySelector('svg');
        if (svg) svg.remove();

        const filteredLinks = { closed: [], open: [] };
        (data.edges || []).forEach(e => {
            const wire = { from: e.source, to: e.target, type: e.type };
            if (e.type === 'open') filteredLinks.open.push(wire);
            else filteredLinks.closed.push(wire);
        });

        // Temporarily swap links for buildGraph
        const origLinks = links;
        links = filteredLinks;
        buildGraph();
        links = origLinks;

        // Show filter stats
        const statsEl = document.getElementById('gf-stats');
        if (statsEl && data.stats) {
            statsEl.style.display = 'block';
            statsEl.innerHTML = `Showing ${data.stats.filtered_wires} of ${data.stats.total_wires} wires<br>${data.stats.nodes} nodes`;
        }
    } catch (e) { console.error('Graph filter error:', e); }
}

function resetGraphFilters() {
    document.getElementById('gf-wire-type').value = '';
    document.getElementById('gf-layer').value = '';
    document.getElementById('gf-module').value = '';
    document.getElementById('gf-min-conn').value = '0';
    const statsEl = document.getElementById('gf-stats');
    if (statsEl) statsEl.style.display = 'none';
    const svg = document.getElementById('graph-container').querySelector('svg');
    if (svg) svg.remove();
    buildGraph();
}

function buildGraph() {
    const container = document.getElementById('graph-container');
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight || 600;

    const nodeMap = new Map();
    const graphLinks = [];

    function addNode(name, type) {
        if (!nodeMap.has(name)) {
            nodeMap.set(name, { id: name, type, connections: 0 });
        }
        nodeMap.get(name).connections++;
    }

    (links.closed || []).forEach(w => {
        addNode(w.from, 'file');
        addNode(w.to, 'target');
        graphLinks.push({ source: w.from, target: w.to, type: 'closed', wireType: w.type });
    });

    (links.open || []).forEach(w => {
        addNode(w.from, 'file');
        addNode(w.to, 'target');
        graphLinks.push({ source: w.from, target: w.to, type: 'open', wireType: w.type });
    });

    const nodes = [...nodeMap.values()];
    if (nodes.length === 0) {
        container.innerHTML += '<div class="loading">No dependency data to visualize.</div>';
        return;
    }

    const svg = d3.select(container).append('svg')
        .attr('width', width)
        .attr('height', height);

    const g = svg.append('g');
    svg.call(d3.zoom().scaleExtent([0.2, 4]).on('zoom', (e) => {
        g.attr('transform', e.transform);
    }));

    const sim = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(graphLinks).id(d => d.id).distance(100))
        .force('charge', d3.forceManyBody().strength(-200))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius(30));

    const link = g.append('g').selectAll('line')
        .data(graphLinks).enter().append('line')
        .attr('stroke', d => d.type === 'closed' ? 'var(--green)' : 'var(--orange)')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', d => d.type === 'open' ? '4,3' : 'none')
        .attr('stroke-opacity', 0.5);

    const node = g.append('g').selectAll('g')
        .data(nodes).enter().append('g')
        .call(d3.drag()
            .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
            .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
            .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
        );

    node.append('circle')
        .attr('r', d => 4 + Math.min(d.connections * 1.5, 8))
        .attr('fill', d => d.type === 'file' ? 'var(--accent)' : 'var(--text-muted)')
        .attr('stroke', 'var(--bg)')
        .attr('stroke-width', 1.5)
        .style('cursor', 'pointer')
        .on('click', (e, d) => {
            const sec = sections.find(s => s.file === d.id || s.id === d.id);
            if (sec) {
                switchView('document');
                setTimeout(() => scrollToSection(sec.id), 100);
            } else {
                openFile(d.id);
            }
        });

    node.append('text')
        .attr('dx', 12)
        .attr('dy', 4)
        .style('font-size', '10px')
        .style('fill', 'var(--text-muted)')
        .style('pointer-events', 'none')
        .text(d => {
            const parts = d.id.split('/');
            return parts[parts.length - 1];
        });

    node.append('title').text(d => d.id);

    sim.on('tick', () => {
        link
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
        node.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

// ── Doc Diff view ────────────────────────────────────────────────────────────
let diffSnapshots = [];

async function renderDiffView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;" class="diff-container">
        <div class="diff-header">
        <h2 style="font-size:18px;font-weight:600;">Documentation Diff</h2>
        <div style="display:flex;gap:8px;align-items:center;">
            <select class="diff-select" id="diff-snap1"></select>
            <span style="color:var(--text-muted);">vs</span>
            <select class="diff-select" id="diff-snap2"></select>
            <button class="tool-btn primary" onclick="loadDiff()">Compare</button>
        </div>
        </div>
        <div id="diff-results">
        <div class="loading" style="color:var(--text-muted);">Loading snapshots...</div>
        </div>
    </div>
    `;

    try {
        const res = await fetch('/api/doc-diff/snapshots');
        if (!res.ok) throw new Error('Failed to load snapshots');
        const data = await res.json();
        diffSnapshots = data.snapshots || [];

        if (diffSnapshots.length < 2) {
            document.getElementById('diff-results').innerHTML = `
        <div class="empty-state">
            <div class="empty-icon"><i data-lucide="git-compare"></i></div>
            <h3>Not enough snapshots</h3>
            <p>Run CodiLay at least twice to see documentation changes between versions.</p>
            <p style="margin-top:8px;font-size:12px;">Snapshots found: ${diffSnapshots.length}</p>
        </div>`;
            updateIcons();
            return;
        }

        const sel1 = document.getElementById('diff-snap1');
        const sel2 = document.getElementById('diff-snap2');
        diffSnapshots.forEach((s, i) => {
            // Format label: Date + Commit Short + Commit Msg
            let dateStr = '';
            const fnameParts = s.filename.replace('snapshot_', '').replace('.json', '').split('_');
            if (fnameParts.length >= 2) {
                const date = fnameParts[0];
                const time = fnameParts[1];
                dateStr = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)} ${time.slice(0, 2)}:${time.slice(2, 4)}`;
            }

            let commitInfo = '';
            if (s.commit) {
                commitInfo = ` (${s.commit.slice(0, 7)})`;
                if (s.commit_msg) {
                    const msg = s.commit_msg.length > 40 ? s.commit_msg.slice(0, 37) + '...' : s.commit_msg;
                    commitInfo += ` ${msg}`;
                }
            }

            const label = `${dateStr}${commitInfo}` || s.filename;

            sel1.innerHTML += `<option value="${escHtml(s.filename)}" ${i === diffSnapshots.length - 2 ? 'selected' : ''}>${escHtml(label)}</option>`;
            sel2.innerHTML += `<option value="${escHtml(s.filename)}" ${i === diffSnapshots.length - 1 ? 'selected' : ''}>${escHtml(label)}</option>`;
        });

        // Auto-load latest diff
        await loadDiff();
    } catch (e) {
        document.getElementById('diff-results').innerHTML = `
        <div class="empty-state">
        <div class="empty-icon"><i data-lucide="alert-circle"></i></div>
        <h3>Could not load diff</h3>
        <p>${escHtml(e.message)}</p>
        </div>`;
        updateIcons();
    }
}

async function loadDiff() {
    const resultsEl = document.getElementById('diff-results');
    resultsEl.innerHTML = '<div class="loading" style="color:var(--text-muted);">Computing diff...</div>';

    const s1 = document.getElementById('diff-snap1')?.value;
    const s2 = document.getElementById('diff-snap2')?.value;
    let url = '/api/doc-diff';
    if (s1 && s2) {
        url += `?snap1=${encodeURIComponent(s1)}&snap2=${encodeURIComponent(s2)}`;
    }

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error('Diff computation failed');
        const data = await res.json();

        if (data.has_changes === false) {
            resultsEl.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon"><i data-lucide="check-circle"></i></div>
            <h3>No changes detected</h3>
            <p>${data.message || 'The documentation is identical between these snapshots.'}</p>
        </div>`;
            updateIcons();
            return;
        }

        let html = '';

        // Time range & Commit info
        if (data.old_run_time || data.new_run_time) {
            html += `<div style="font-size:12px;color:var(--text-muted);margin-bottom:16px;line-height:1.5;">`;
            html += `<div>${data.old_run_time || '?'} &rarr; ${data.new_run_time || '?'}</div>`;
            if (data.old_commit_msg || data.new_commit_msg) {
                html += `<div style="margin-top:4px;">
            <span style="opacity:0.6;">From:</span> ${escHtml(data.old_commit_msg || 'n/a')}<br/>
            <span style="opacity:0.6;">To:</span> &nbsp;&nbsp;&nbsp;${escHtml(data.new_commit_msg || 'n/a')}
        </div>`;
            }
            html += `</div>`;
        }

        // Stats delta
        if (data.stats_delta) {
            const fd = data.stats_delta.files_processed;
            const sd = data.stats_delta.sections;
            if (fd !== 0 || sd !== 0) {
                html += `<div style="display:flex;gap:16px;margin-bottom:20px;">`;
                if (fd !== 0) html += `<div class="diff-badge ${fd > 0 ? 'added' : 'removed'}">${fd > 0 ? '+' : ''}${fd} files</div>`;
                if (sd !== 0) html += `<div class="diff-badge ${sd > 0 ? 'added' : 'removed'}">${sd > 0 ? '+' : ''}${sd} sections</div>`;
                html += `</div>`;
            }
        }

        // Wire changes
        const wc = data.wire_changes || {};
        if (wc.new_closed || wc.lost_closed || wc.new_open || wc.resolved_open) {
            html += `<div class="diff-section"><div class="diff-section-header">
        <span class="diff-badge modified">wires</span>
        <span class="diff-section-title">Wire Changes</span>
        </div><div class="diff-section-body">`;
            if (wc.new_closed) html += `<span class="diff-line-added">+${wc.new_closed} newly closed wires</span>`;
            if (wc.lost_closed) html += `<span class="diff-line-removed">-${wc.lost_closed} lost closed wires</span>`;
            if (wc.new_open) html += `<span class="diff-line-removed">+${wc.new_open} new open wires</span>`;
            if (wc.resolved_open) html += `<span class="diff-line-added">${wc.resolved_open} open wires resolved</span>`;
            html += `</div></div>`;
        }

        // Added sections
        (data.added_sections || []).forEach(s => {
            html += `<div class="diff-section">
        <div class="diff-section-header">
            <span class="diff-badge added">added</span>
            <span class="diff-section-title">${escHtml(s.title)}</span>
        </div>
        <div class="diff-section-body">${escHtml(s.summary || 'New section added')}</div>
        </div>`;
        });

        // Removed sections
        (data.removed_sections || []).forEach(s => {
            html += `<div class="diff-section">
        <div class="diff-section-header">
            <span class="diff-badge removed">removed</span>
            <span class="diff-section-title">${escHtml(s.title)}</span>
        </div>
        </div>`;
        });

        // Modified sections
        (data.modified_sections || []).forEach(s => {
            html += `<div class="diff-section">
        <div class="diff-section-header">
            <span class="diff-badge modified">modified</span>
            <span class="diff-section-title">${escHtml(s.title)}</span>
        </div>
        <div class="diff-section-body">
            ${s.summary ? `<p style="margin-bottom:8px;">${escHtml(s.summary)}</p>` : ''}
            ${(s.diff || []).map(line => {
                if (line.startsWith('+')) return `<span class="diff-line-added">${escHtml(line)}</span>`;
                if (line.startsWith('-')) return `<span class="diff-line-removed">${escHtml(line)}</span>`;
                return `<span style="display:block;color:var(--text-muted);">${escHtml(line)}</span>`;
            }).join('')}
        </div>
        </div>`;
        });

        if (!html) {
            html = `<div class="empty-state">
        <div class="empty-icon"><i data-lucide="check-circle"></i></div>
        <h3>No changes</h3>
        <p>Documentation is identical between these snapshots.</p>
        </div>`;
        }

        resultsEl.innerHTML = html;
        updateIcons();
    } catch (e) {
        resultsEl.innerHTML = `<div style="color:var(--red);padding:20px;">Error: ${escHtml(e.message)}</div>`;
    }
}

// ── Diff-Run view ────────────────────────────────────────────────────────────
async function renderDiffRunView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;" class="diff-run-container">
        <div class="diff-header">
            <h2 style="font-size:18px;font-weight:600;">Diff-Run — Document Changes Since Boundary</h2>
            <p style="color:var(--text-muted);font-size:13px;margin-top:8px;">
                Generate focused documentation for code changes since a specific commit, tag, branch, or date.
            </p>
        </div>
        
        <div style="margin-top:24px;background:var(--bg-secondary);padding:20px;border-radius:12px;border:1px solid var(--border);">
            <h3 style="font-size:14px;font-weight:600;margin-bottom:16px;">Boundary Options</h3>
            
            <div style="display:grid;gap:16px;">
                <div>
                    <label style="display:block;font-size:12px;font-weight:500;margin-bottom:6px;color:var(--text-muted);">
                        Boundary Type
                    </label>
                    <select id="diff-run-type" onchange="toggleDiffRunInputs()" 
                            style="width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;">
                        <option value="commit">Commit Hash</option>
                        <option value="tag">Tag</option>
                        <option value="branch" selected>Branch (merge-base)</option>
                        <option value="date">Date</option>
                    </select>
                </div>
                
                <div>
                    <label style="display:block;font-size:12px;font-weight:500;margin-bottom:6px;color:var(--text-muted);">
                        <span id="diff-run-input-label">Branch Name</span>
                    </label>
                    <input type="text" id="diff-run-value" placeholder="main" value="main"
                           style="width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;">
                    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;" id="diff-run-hint">
                        Compare current branch against main (finds merge base)
                    </div>
                </div>
                
                <div style="display:flex;align-items:center;gap:12px;">
                    <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer;">
                        <input type="checkbox" id="diff-run-update-doc" style="cursor:pointer;">
                        <span>Update CODEBASE.md with changes</span>
                    </label>
                </div>
                
                <button class="tool-btn primary" onclick="runDiffRun()" style="width:fit-content;">
                    <i data-lucide="git-branch" style="width:14px;height:14px;"></i>
                    Generate Change Report
                </button>
            </div>
        </div>
        
        <div id="diff-run-results" style="margin-top:24px;">
            <div class="empty-state">
                <div class="empty-icon"><i data-lucide="git-branch"></i></div>
                <h3>Ready to analyze changes</h3>
                <p>Select a boundary and click "Generate Change Report" to document what changed.</p>
                <div style="margin-top:16px;text-align:left;max-width:500px;font-size:12px;color:var(--text-muted);line-height:1.6;">
                    <strong>Use cases:</strong><br>
                    • <strong>Branch:</strong> Document changes before submitting a PR<br>
                    • <strong>Tag:</strong> Generate release notes between versions<br>
                    • <strong>Date:</strong> See what changed this month<br>
                    • <strong>Commit:</strong> Analyze changes since a specific commit
                </div>
            </div>
        </div>
    </div>
    `;
    updateIcons();
}

function toggleDiffRunInputs() {
    const type = document.getElementById('diff-run-type').value;
    const label = document.getElementById('diff-run-input-label');
    const input = document.getElementById('diff-run-value');
    const hint = document.getElementById('diff-run-hint');
    
    switch(type) {
        case 'commit':
            label.textContent = 'Commit Hash';
            input.placeholder = 'abc123f';
            input.value = '';
            hint.textContent = 'Enter a commit hash (short or full)';
            break;
        case 'tag':
            label.textContent = 'Tag Name';
            input.placeholder = 'v2.1.0';
            input.value = '';
            hint.textContent = 'Enter a git tag (e.g., v2.1.0)';
            break;
        case 'branch':
            label.textContent = 'Branch Name';
            input.placeholder = 'main';
            input.value = 'main';
            hint.textContent = 'Compare current branch against this branch (finds merge base)';
            break;
        case 'date':
            label.textContent = 'Date (YYYY-MM-DD)';
            input.placeholder = '2024-03-01';
            input.value = '';
            hint.textContent = 'Show changes after this date';
            break;
    }
}

async function runDiffRun() {
    const resultsEl = document.getElementById('diff-run-results');
    const type = document.getElementById('diff-run-type').value;
    const value = document.getElementById('diff-run-value').value.trim();
    const updateDoc = document.getElementById('diff-run-update-doc').checked;
    
    if (!value) {
        resultsEl.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon"><i data-lucide="alert-circle"></i></div>
                <h3>Missing boundary value</h3>
                <p>Please enter a ${type} to analyze changes.</p>
            </div>
        `;
        updateIcons();
        return;
    }
    
    resultsEl.innerHTML = '<div class="loading">Analyzing changes and generating report...</div>';
    
    try {
        const params = new URLSearchParams();
        if (type === 'branch') {
            params.append('since_branch', value);
        } else {
            params.append('since', value);
        }
        if (updateDoc) {
            params.append('update_doc', 'true');
        }
        
        const res = await fetch(`/api/diff-run?${params.toString()}`);
        const data = await res.json();
        
        if (!res.ok) {
            throw new Error(data.error || 'Failed to generate change report');
        }
        
        // Display results
        let html = `
            <div class="diff-section" style="background:var(--bg-success);border:1px solid var(--green);padding:16px;border-radius:12px;margin-bottom:20px;">
                <div style="display:flex;align-items:center;gap:12px;">
                    <i data-lucide="check-circle" style="color:var(--green);width:20px;height:20px;"></i>
                    <div>
                        <div style="font-weight:600;color:var(--green);">Change Report Generated</div>
                        <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">
                            ${escHtml(data.report_path || 'Report saved')}
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Summary
        if (data.summary) {
            html += `
                <div class="diff-section">
                    <div class="diff-section-header">
                        <span class="diff-section-title">Summary</span>
                    </div>
                    <div class="diff-section-body">
                        ${escHtml(data.summary)}
                    </div>
                </div>
            `;
        }
        
        // Changes stats
        if (data.changes) {
            const c = data.changes;
            html += `
                <div class="diff-section">
                    <div class="diff-section-header">
                        <span class="diff-section-title">Changes Detected</span>
                    </div>
                    <div class="diff-section-body" style="display:flex;gap:12px;flex-wrap:wrap;">
                        ${c.added > 0 ? `<div class="diff-badge added">+${c.added} added</div>` : ''}
                        ${c.modified > 0 ? `<div class="diff-badge modified">~${c.modified} modified</div>` : ''}
                        ${c.deleted > 0 ? `<div class="diff-badge removed">-${c.deleted} deleted</div>` : ''}
                        ${c.renamed > 0 ? `<div class="diff-badge">→${c.renamed} renamed</div>` : ''}
                        <div class="diff-badge">${c.commits || 0} commits</div>
                    </div>
                </div>
            `;
        }
        
        // LLM usage
        if (data.llm_usage) {
            const u = data.llm_usage;
            html += `
                <div style="margin-top:16px;padding:12px;background:var(--bg-secondary);border-radius:8px;font-size:12px;color:var(--text-muted);">
                    LLM usage: ${u.calls || 0} calls, ${(u.input_tokens || 0).toLocaleString()} input tokens, ${(u.output_tokens || 0).toLocaleString()} output tokens
                </div>
            `;
        }
        
        resultsEl.innerHTML = html;
        updateIcons();
        
    } catch (e) {
        resultsEl.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon"><i data-lucide="alert-circle"></i></div>
                <h3>Error generating report</h3>
                <p style="color:var(--red);">${escHtml(e.message)}</p>
            </div>
        `;
        updateIcons();
    }
}

// ── Search view ──────────────────────────────────────────────────────────────
let searchDebounce = null;

async function renderSearchView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;" class="search-panel">
        <h2 style="font-size:18px;font-weight:600;margin-bottom:20px;">Conversation Search</h2>
        <div class="search-bar">
        <input type="text" id="conv-search-input" placeholder="Search across all conversations..."
            onkeyup="handleSearchKeyup(event)">
        <select id="conv-search-role" style="padding:10px 12px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;">
            <option value="">All roles</option>
            <option value="user">User only</option>
            <option value="assistant">Assistant only</option>
        </select>
        <button class="tool-btn primary" onclick="runConvSearch()">Search</button>
        </div>
        <div id="conv-search-meta" style="font-size:12px;color:var(--text-muted);margin-bottom:16px;display:none;"></div>
        <div id="conv-search-results">
        <div class="empty-state">
            <div class="empty-icon"><i data-lucide="search"></i></div>
            <h3>Search conversations</h3>
            <p>Enter a query to search across all past chat conversations.</p>
        </div>
        </div>
        <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border);">
        <button class="tool-btn" onclick="rebuildSearchIndex()">
            <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> Rebuild Index
        </button>
        </div>
    </div>
    `;
    updateIcons();
}

function handleSearchKeyup(e) {
    if (e.key === 'Enter') {
        runConvSearch();
        return;
    }
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
        const q = document.getElementById('conv-search-input').value.trim();
        if (q.length >= 3) runConvSearch();
    }, 500);
}

async function runConvSearch() {
    const q = document.getElementById('conv-search-input').value.trim();
    if (!q) return;
    const role = document.getElementById('conv-search-role').value;
    const resultsEl = document.getElementById('conv-search-results');
    const metaEl = document.getElementById('conv-search-meta');
    resultsEl.innerHTML = '<div class="loading" style="color:var(--text-muted);">Searching...</div>';

    try {
        let url = `/api/search?q=${encodeURIComponent(q)}&top_k=30`;
        if (role) url += `&role=${encodeURIComponent(role)}`;

        const res = await fetch(url);
        if (!res.ok) throw new Error('Search failed');
        const data = await res.json();

        metaEl.style.display = 'block';
        metaEl.textContent = `${data.total_results || 0} results across ${data.total_conversations_searched || 0} conversations (${data.total_messages_searched || 0} messages indexed)`;

        if (!data.results || data.results.length === 0) {
            resultsEl.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon"><i data-lucide="search-x"></i></div>
            <h3>No results</h3>
            <p>No messages matched "${escHtml(q)}". Try different keywords.</p>
        </div>`;
            updateIcons();
            return;
        }

        resultsEl.innerHTML = data.results.map(r => `
        <div class="search-result" onclick="searchResultClick('${escAttr(r.conversation_id)}')">
        <div class="search-result-header">
            <span class="search-result-conv">${escHtml(r.conversation_title || r.conversation_id)}</span>
            <div class="search-result-meta">
            <span style="text-transform:capitalize;">${escHtml(r.role)}</span>
            ${r.created_at ? `<span>${escHtml(r.created_at)}</span>` : ''}
            <span class="search-score">${r.score.toFixed(3)}</span>
            </div>
        </div>
        <div class="search-result-content">${r.snippet || escHtml((r.content || '').slice(0, 200))}</div>
        </div>
    `).join('');
        updateIcons();
    } catch (e) {
        resultsEl.innerHTML = `<div style="color:var(--red);padding:20px;">Error: ${escHtml(e.message)}</div>`;
    }
}

function searchResultClick(convId) {
    // Switch to chat and load that conversation if possible
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        addChatMsg(`Viewing conversation: ${convId}`, 'bot');
    }
}

async function rebuildSearchIndex() {
    try {
        const res = await fetch('/api/search/rebuild', { method: 'POST' });
        if (res.ok) {
            addChatMsg('Search index rebuilt successfully.', 'bot');
        }
    } catch (e) { console.error('Rebuild failed:', e); }
}

// ── Team memory view ─────────────────────────────────────────────────────────
let teamTab = 'facts';

async function renderTeamView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;" class="team-container">
        <h2 style="font-size:18px;font-weight:600;margin-bottom:20px;">Team Knowledge Base</h2>
        <div class="team-tabs">
        <button class="team-tab ${teamTab === 'facts' ? 'active' : ''}" onclick="switchTeamTab('facts')">Facts</button>
        <button class="team-tab ${teamTab === 'decisions' ? 'active' : ''}" onclick="switchTeamTab('decisions')">Decisions</button>
        <button class="team-tab ${teamTab === 'conventions' ? 'active' : ''}" onclick="switchTeamTab('conventions')">Conventions</button>
        <button class="team-tab ${teamTab === 'annotations' ? 'active' : ''}" onclick="switchTeamTab('annotations')">Annotations</button>
        <button class="team-tab ${teamTab === 'users' ? 'active' : ''}" onclick="switchTeamTab('users')">Users</button>
        </div>
        <div id="team-content">
        <div class="loading" style="color:var(--text-muted);">Loading...</div>
        </div>
    </div>
    `;
    await loadTeamTab();
}

function switchTeamTab(tab) {
    teamTab = tab;
    document.querySelectorAll('.team-tab').forEach(t => {
        t.classList.toggle('active', t.textContent.toLowerCase() === tab);
    });
    loadTeamTab();
}

async function loadTeamTab() {
    const el = document.getElementById('team-content');
    if (!el) return;
    el.innerHTML = '<div class="loading" style="color:var(--text-muted);">Loading...</div>';

    try {
        if (teamTab === 'facts') await renderTeamFacts(el);
        else if (teamTab === 'decisions') await renderTeamDecisions(el);
        else if (teamTab === 'conventions') await renderTeamConventions(el);
        else if (teamTab === 'annotations') await renderTeamAnnotations(el);
        else if (teamTab === 'users') await renderTeamUsers(el);
    } catch (e) {
        el.innerHTML = `<div style="color:var(--red);padding:20px;">Error: ${escHtml(e.message)}</div>`;
    }
    updateIcons();
}

async function renderTeamFacts(el) {
    const res = await fetch('/api/team/facts');
    const data = await res.json();
    const facts = data.facts || [];

    let html = `<div style="margin-bottom:16px;">
    <button class="tool-btn primary" onclick="teamAddFact()"><i data-lucide="plus" style="width:14px;height:14px;"></i> Add Fact</button>
    </div>`;

    if (facts.length === 0) {
        html += `<div class="empty-state">
        <div class="empty-icon"><i data-lucide="lightbulb"></i></div>
        <h3>No facts yet</h3>
        <p>Add team knowledge that should be shared across all members.</p>
    </div>`;
    } else {
        facts.forEach(f => {
            html += `<div class="team-item">
        <div class="team-item-header">
            <span style="font-size:11px;padding:2px 8px;background:var(--accent-dim);color:var(--accent);border-radius:4px;">${escHtml(f.category || 'general')}</span>
            <div style="display:flex;gap:4px;">
            <button class="vote-btn" onclick="teamVoteFact('${escAttr(f.id)}', 'up')">+${f.upvotes || 0}</button>
            <button class="vote-btn" onclick="teamVoteFact('${escAttr(f.id)}', 'down')">-${f.downvotes || 0}</button>
            <button class="triage-delete-btn" onclick="teamDeleteFact('${escAttr(f.id)}')"><i data-lucide="trash-2" style="width:14px;height:14px;"></i></button>
            </div>
        </div>
        <div class="team-item-content">${escHtml(f.fact)}</div>
        <div class="team-item-meta">
            ${f.author ? `<span>by ${escHtml(f.author)}</span>` : ''}
            ${f.created_at ? `<span>${escHtml(f.created_at)}</span>` : ''}
            ${(f.tags || []).length > 0 ? `<span>${f.tags.map(t => `#${escHtml(t)}`).join(' ')}</span>` : ''}
        </div>
        </div>`;
        });
    }
    el.innerHTML = html;
}

async function teamAddFact() {
    const fact = prompt('Enter the fact:');
    if (!fact) return;
    const category = prompt('Category (general, architecture, convention, gotcha):', 'general') || 'general';
    const author = prompt('Your name (optional):', '') || '';
    try {
        await fetch('/api/team/facts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fact, category, author }),
        });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function teamVoteFact(id, vote) {
    try {
        await fetch(`/api/team/facts/${encodeURIComponent(id)}/vote?vote=${vote}`, { method: 'POST' });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function teamDeleteFact(id) {
    if (!confirm('Delete this fact?')) return;
    try {
        await fetch(`/api/team/facts/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function renderTeamDecisions(el) {
    const res = await fetch('/api/team/decisions');
    const data = await res.json();
    const decisions = data.decisions || [];

    let html = `<div style="margin-bottom:16px;">
    <button class="tool-btn primary" onclick="teamAddDecision()"><i data-lucide="plus" style="width:14px;height:14px;"></i> Add Decision</button>
    </div>`;

    if (decisions.length === 0) {
        html += `<div class="empty-state">
        <div class="empty-icon"><i data-lucide="gavel"></i></div>
        <h3>No decisions recorded</h3>
        <p>Document architectural and design decisions for the team.</p>
    </div>`;
    } else {
        decisions.forEach(d => {
            const statusColor = d.status === 'accepted' ? 'var(--green)' : d.status === 'rejected' ? 'var(--red)' : 'var(--orange)';
            html += `<div class="team-item">
        <div class="team-item-header">
            <span class="diff-section-title">${escHtml(d.title)}</span>
            <span style="font-size:11px;padding:2px 8px;border-radius:4px;background:${statusColor}20;color:${statusColor};">${escHtml(d.status || 'proposed')}</span>
        </div>
        <div class="team-item-content">${escHtml(d.description)}</div>
        <div class="team-item-meta">
            ${d.author ? `<span>by ${escHtml(d.author)}</span>` : ''}
            ${d.created_at ? `<span>${escHtml(d.created_at)}</span>` : ''}
            ${(d.related_files || []).length > 0 ? `<span>${d.related_files.map(f => escHtml(f)).join(', ')}</span>` : ''}
        </div>
        </div>`;
        });
    }
    el.innerHTML = html;
}

async function teamAddDecision() {
    const title = prompt('Decision title:');
    if (!title) return;
    const description = prompt('Description:');
    if (!description) return;
    const author = prompt('Your name (optional):', '') || '';
    try {
        await fetch('/api/team/decisions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description, author }),
        });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function renderTeamConventions(el) {
    const res = await fetch('/api/team/conventions');
    const data = await res.json();
    const conventions = data.conventions || [];

    let html = `<div style="margin-bottom:16px;">
    <button class="tool-btn primary" onclick="teamAddConvention()"><i data-lucide="plus" style="width:14px;height:14px;"></i> Add Convention</button>
    </div>`;

    if (conventions.length === 0) {
        html += `<div class="empty-state">
        <div class="empty-icon"><i data-lucide="book-open"></i></div>
        <h3>No conventions defined</h3>
        <p>Document coding standards and naming conventions for consistency.</p>
    </div>`;
    } else {
        conventions.forEach(c => {
            html += `<div class="team-item">
        <div class="team-item-header">
            <span class="diff-section-title">${escHtml(c.name)}</span>
        </div>
        <div class="team-item-content">${escHtml(c.description)}</div>
        ${(c.examples || []).length > 0 ? `<div style="margin-top:8px;padding:8px 12px;background:var(--bg);border-radius:4px;font-family:monospace;font-size:12px;color:var(--text-muted);">
            ${c.examples.map(ex => escHtml(ex)).join('<br>')}
        </div>` : ''}
        <div class="team-item-meta">
            ${c.author ? `<span>by ${escHtml(c.author)}</span>` : ''}
        </div>
        </div>`;
        });
    }
    el.innerHTML = html;
}

async function teamAddConvention() {
    const name = prompt('Convention name:');
    if (!name) return;
    const description = prompt('Description:');
    if (!description) return;
    const author = prompt('Your name (optional):', '') || '';
    try {
        await fetch('/api/team/conventions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, author }),
        });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function renderTeamAnnotations(el) {
    const res = await fetch('/api/team/annotations');
    const data = await res.json();
    const annotations = data.annotations || [];

    let html = `<div style="margin-bottom:16px;">
    <button class="tool-btn primary" onclick="teamAddAnnotation()"><i data-lucide="plus" style="width:14px;height:14px;"></i> Add Annotation</button>
    </div>`;

    if (annotations.length === 0) {
        html += `<div class="empty-state">
        <div class="empty-icon"><i data-lucide="message-square"></i></div>
        <h3>No annotations</h3>
        <p>Annotate specific files or line ranges with team notes.</p>
    </div>`;
    } else {
        annotations.forEach(a => {
            html += `<div class="team-item">
        <div class="team-item-header">
            <span style="font-family:monospace;font-size:13px;color:var(--accent);cursor:pointer;" onclick="openFile('${escAttr(a.file_path)}')">${escHtml(a.file_path)}${a.line_range ? `:${escHtml(a.line_range)}` : ''}</span>
            <button class="triage-delete-btn" onclick="teamDeleteAnnotation('${escAttr(a.id)}')"><i data-lucide="trash-2" style="width:14px;height:14px;"></i></button>
        </div>
        <div class="team-item-content">${escHtml(a.note)}</div>
        <div class="team-item-meta">
            ${a.author ? `<span>by ${escHtml(a.author)}</span>` : ''}
            ${a.created_at ? `<span>${escHtml(a.created_at)}</span>` : ''}
        </div>
        </div>`;
        });
    }
    el.innerHTML = html;
}

async function teamAddAnnotation() {
    const file_path = prompt('File path:');
    if (!file_path) return;
    const note = prompt('Annotation note:');
    if (!note) return;
    const line_range = prompt('Line range (optional, e.g. 10-25):', '') || '';
    const author = prompt('Your name (optional):', '') || '';
    try {
        await fetch('/api/team/annotations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path, note, author, line_range: line_range || null }),
        });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function teamDeleteAnnotation(id) {
    if (!confirm('Delete this annotation?')) return;
    try {
        await fetch(`/api/team/annotations/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

async function renderTeamUsers(el) {
    const res = await fetch('/api/team/users');
    const data = await res.json();
    const users = data.users || [];

    let html = `<div style="margin-bottom:16px;">
    <button class="tool-btn primary" onclick="teamAddUser()"><i data-lucide="user-plus" style="width:14px;height:14px;"></i> Register User</button>
    </div>`;

    if (users.length === 0) {
        html += `<div class="empty-state">
        <div class="empty-icon"><i data-lucide="users"></i></div>
        <h3>No team members</h3>
        <p>Register team members to track contributions.</p>
    </div>`;
    } else {
        users.forEach(u => {
            html += `<div class="team-item" style="display:flex;align-items:center;gap:12px;">
        <div style="width:36px;height:36px;border-radius:50%;background:var(--accent-dim);display:flex;align-items:center;justify-content:center;color:var(--accent);font-weight:600;font-size:14px;">${escHtml((u.display_name || u.username || '?')[0].toUpperCase())}</div>
        <div>
            <div style="font-weight:500;font-size:14px;">${escHtml(u.display_name || u.username)}</div>
            <div style="font-size:12px;color:var(--text-muted);">@${escHtml(u.username)}${u.joined_at ? ` &middot; joined ${escHtml(u.joined_at)}` : ''}</div>
        </div>
        </div>`;
        });
    }
    el.innerHTML = html;
}

async function teamAddUser() {
    const username = prompt('Username:');
    if (!username) return;
    const display_name = prompt('Display name (optional):', '') || '';
    try {
        await fetch('/api/team/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, display_name }),
        });
        await loadTeamTab();
    } catch (e) { console.error(e); }
}

// ── Audit view ───────────────────────────────────────────────────────────────
async function renderAuditView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 24px;">
            <div>
                <h2 style="font-size:18px;font-weight:600;margin-bottom:8px;">System Audit</h2>
                <p style="color:var(--text-muted);font-size:13px;max-width:600px;">
                    Run AI-powered audits against your architecture. Passive mode uses existing context (fast). Active mode deeply inspects files (thorough).
                </p>
            </div>
            <div style="display:flex; gap:12px; background:var(--bg-secondary); padding:16px; border-radius:8px; border:1px solid var(--border);">
                <div>
                    <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px;">Audit Type</label>
                    <select id="audit-type-select" style="padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;">
                        <option value="security">Security</option>
                        <option value="performance">Performance</option>
                        <option value="architecture">Architecture</option>
                        <option value="code_quality">Code Quality</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px;">Mode</label>
                    <select id="audit-mode-select" style="padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;">
                        <option value="passive">Passive (Fast)</option>
                        <option value="active">Active (Deep)</option>
                    </select>
                </div>
                <div style="display:flex; align-items:flex-end;">
                    <button class="tool-btn primary" onclick="runAudit()"><i data-lucide="play" style="width:14px;height:14px;"></i> Run Audit</button>
                </div>
            </div>
        </div>

        <div id="audit-history-container" style="margin-bottom: 32px;">
            <h3 style="font-size:14px;font-weight:600;margin-bottom:12px;color:var(--text-muted);">Past Audits</h3>
            <div id="audit-history-list">Loading history...</div>
        </div>

        <div id="audit-results-container" style="display:none;background:var(--bg);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
            <div style="padding:16px;background:var(--bg-secondary);border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
                <h3 id="audit-results-title" style="font-size:14px;font-weight:600;">Audit Results</h3>
            </div>
            <div id="audit-results-content" style="padding:24px;font-size:13px;line-height:1.6;overflow:auto;"></div>
        </div>
    </div>
    `;
    updateIcons();
    loadAuditHistory();
}

async function loadAuditHistory() {
    const listEl = document.getElementById('audit-history-list');
    try {
        const res = await fetch('/api/audits');
        if (!res.ok) throw new Error('Failed to load audits');
        const data = await res.json();
        const runs = data.runs || [];

        if (runs.length === 0) {
            listEl.innerHTML = '<div style="font-size:13px;color:var(--text-muted);padding:16px;background:var(--bg-secondary);border-radius:6px;border:1px dashed var(--border);">No past audits found. Run one above to get started.</div>';
            return;
        }

        let html = '<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:16px;">';
        runs.slice().reverse().forEach((run, i) => {
            const date = new Date(run.date).toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
            });
            html += `
            <div class="tool-card" style="cursor:pointer;" onclick="viewAuditReport('${escAttr(run.report_file)}')">
                <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                    <span style="font-weight:600;capitalize;">${escHtml(run.type)}</span>
                    <span style="font-size:11px;color:var(--text-muted);background:var(--bg);padding:2px 6px;border-radius:4px;">${escHtml(run.mode)}</span>
                </div>
                <div style="font-size:12px;color:var(--text-muted);"><i data-lucide="calendar" style="width:12px;height:12px;display:inline-block;vertical-align:-2px;margin-right:4px;"></i>${escHtml(date)}</div>
            </div>`;
        });
        html += '</div>';
        listEl.innerHTML = html;
        updateIcons();
    } catch(e) {
        listEl.innerHTML = `<span style="color:var(--red);">${escHtml(e.message)}</span>`;
    }
}

async function runAudit() {
    const type = document.getElementById('audit-type-select').value;
    const mode = document.getElementById('audit-mode-select').value;
    
    const resEl = document.getElementById('audit-results-container');
    const contentEl = document.getElementById('audit-results-content');
    const titleEl = document.getElementById('audit-results-title');
    
    resEl.style.display = 'block';
    titleEl.innerHTML = `Running ${type.toUpperCase()} Audit...`;
    contentEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;color:var(--accent);">
            <div class="spinner" style="border-right-color:var(--accent);width:20px;height:20px;"></div>
            <span>Analyzing architecture, code, and wires. This may take a moment.</span>
        </div>
    `;

    try {
        const res = await fetch('/api/audits', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ audit_type: type, mode: mode })
        });
        if (!res.ok) throw new Error('Audit failed');
        const data = await res.json();
        
        titleEl.textContent = `${type.toUpperCase()} Audit Complete`;
        contentEl.innerHTML = renderAuditFindings(data.response);
        updateIcons();
        loadAuditHistory(); // Refresh list
    } catch(e) {
        contentEl.innerHTML = `<div style="color:var(--red);">${escHtml(e.message)}</div>`;
    }
}

async function viewAuditReport(filename) {
    const resEl = document.getElementById('audit-results-container');
    const contentEl = document.getElementById('audit-results-content');
    const titleEl = document.getElementById('audit-results-title');
    
    resEl.style.display = 'block';
    titleEl.textContent = `Loading...`;
    contentEl.innerHTML = `<div class="spinner"></div>`;
    
    try {
        const res = await fetch(`/api/audits/${filename}`);
        if (!res.ok) throw new Error('Failed to load report');
        const data = await res.json();
        
        titleEl.textContent = filename;
        contentEl.innerHTML = renderAuditFindings(data.content);
        updateIcons();
    } catch(e) {
        contentEl.innerHTML = `<div style="color:var(--red);">${escHtml(e.message)}</div>`;
    }
}

function renderAuditFindings(markdown) {
    if (!markdown) return '';
    
    const blocks = markdown.split(/\n?(?=FINDING:)/g);
    if (blocks.length <= 1 && !markdown.includes('FINDING:')) {
        return renderMarkdown(markdown);
    }
    
    let findings = [];
    let headerHtml = '';
    
    if (blocks[0] && !blocks[0].trim().startsWith('FINDING:')) {
        headerHtml = `<div style="margin-bottom: 24px; color: var(--text-muted); font-size: 14px; border-bottom: 1px solid var(--border); padding-bottom: 16px;">${renderMarkdown(blocks[0])}</div>`;
        blocks.shift();
    }
    
    blocks.forEach(block => {
        const lines = block.split('\n');
        let finding = { title: '', severity: 'LOW', file: '', wire: '', evidence: '', impact: '', fix: '' };
        let currentField = '';
        
        lines.forEach(line => {
            const trimmed = line.trim();
            if (trimmed.startsWith('FINDING:')) {
                finding.title = trimmed.replace('FINDING:', '').trim();
                currentField = 'title';
            } else if (trimmed.startsWith('Severity:')) {
                finding.severity = trimmed.replace('Severity:', '').trim().toUpperCase();
                currentField = 'severity';
            } else if (trimmed.startsWith('File:')) {
                finding.file = trimmed.replace('File:', '').trim();
                currentField = 'file';
            } else if (trimmed.startsWith('Wire:')) {
                finding.wire = trimmed.replace('Wire:', '').trim();
                currentField = 'wire';
            } else if (trimmed.startsWith('Evidence:')) {
                finding.evidence = trimmed.replace('Evidence:', '').trim();
                currentField = 'evidence';
            } else if (trimmed.startsWith('Impact:')) {
                finding.impact = trimmed.replace('Impact:', '').trim();
                currentField = 'impact';
            } else if (trimmed.startsWith('Fix:')) {
                finding.fix = trimmed.replace('Fix:', '').trim();
                currentField = 'fix';
            } else if (currentField && trimmed) {
                finding[currentField] += ' ' + trimmed;
            }
        });
        if (finding.title) findings.push(finding);
    });

    const stats = {
        total: findings.length,
        high: findings.filter(f => f.severity.includes('HIGH')).length,
        medium: findings.filter(f => f.severity.includes('MEDIUM')).length,
        low: findings.filter(f => f.severity.includes('LOW')).length
    };

    let html = `
    <div class="audit-beautified">
        <div class="audit-dashboard">
            <div class="audit-stat-card">
                <span class="audit-stat-label">Total Findings</span>
                <span class="audit-stat-value">${stats.total}</span>
            </div>
            <div class="audit-stat-card high">
                <span class="audit-stat-label">High Severity</span>
                <span class="audit-stat-value">${stats.high}</span>
            </div>
            <div class="audit-stat-card medium">
                <span class="audit-stat-label">Medium Severity</span>
                <span class="audit-stat-value">${stats.medium}</span>
            </div>
            <div class="audit-stat-card low">
                <span class="audit-stat-label">Low Severity</span>
                <span class="audit-stat-value">${stats.low}</span>
            </div>
        </div>

        <div class="audit-filters">
            <button class="audit-filter-btn active" onclick="filterAuditFindings(this, 'ALL')">All</button>
            <button class="audit-filter-btn" onclick="filterAuditFindings(this, 'HIGH')">High Severity</button>
            <button class="audit-filter-btn" onclick="filterAuditFindings(this, 'MEDIUM')">Medium Severity</button>
            <button class="audit-filter-btn" onclick="filterAuditFindings(this, 'LOW')">Low Severity</button>
        </div>

        ${headerHtml}

        <div id="audit-findings-list">
    `;

    findings.forEach((finding, idx) => {
        const sevClass = finding.severity.toLowerCase().includes('high') ? 'high' : 
                         finding.severity.toLowerCase().includes('medium') ? 'medium' : 'low';
        
        html += `
        <div class="finding-card-wrapper" data-severity="${finding.severity}">
            <div class="finding-card" data-severity="${finding.severity}">
                <div class="finding-header" onclick="toggleFindingCard(this.closest('.finding-card'))">
                    <div style="flex:1; pointer-events:none;">
                        <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                            <span class="severity-badge ${sevClass}">${finding.severity}</span>
                            <span style="font-size:11px; color:var(--text-muted); opacity:0.6;">${escHtml(finding.file)}</span>
                        </div>
                        <h4 class="finding-title">${escHtml(finding.title)}</h4>
                    </div>
                    <i data-lucide="chevron-down" class="expand-icon" style="width:20px;height:20px;"></i>
                </div>
                
                <div class="finding-details">
                    <div class="finding-meta" style="margin-top:16px;">
                        <div class="meta-item" style="cursor:default">
                            <div style="flex:1">
                                <span class="meta-label">Source File</span>
                                <span class="meta-value" onclick="openFile('${escAttr(finding.file)}')"><i data-lucide="file-text" style="width:12px;height:12px"></i> ${escHtml(finding.file)}</span>
                            </div>
                        </div>
                        ${finding.wire ? `
                        <div class="meta-item" style="cursor:default">
                            <div style="flex:1">
                                <span class="meta-label">Architectural Context</span>
                                <span class="meta-value" onclick="scrollToSection('${escAttr(finding.wire)}')"><i data-lucide="network" style="width:12px;height:12px"></i> ${escHtml(finding.wire)}</span>
                            </div>
                        </div>` : ''}
                    </div>
                    
                    <div class="finding-section">
                        <div class="finding-section-title"><i data-lucide="search" style="width:14px;height:14px;"></i> Evidence & Observations</div>
                        <div class="finding-section-content">${renderMarkdown(finding.evidence)}</div>
                    </div>
                    
                    ${finding.impact ? `
                    <div class="finding-section">
                        <div class="finding-section-title"><i data-lucide="activity" style="width:14px;height:14px;"></i> System Impact</div>
                        <div class="finding-section-content">${renderMarkdown(finding.impact)}</div>
                    </div>` : ''}
                    
                    ${finding.fix ? `
                    <div class="fix-box">
                        <div class="finding-section-title"><i data-lucide="check-circle" style="width:14px;height:14px;"></i> Recommended Fix</div>
                        <div class="finding-section-content">${renderMarkdown(finding.fix)}</div>
                    </div>` : ''}
                    <div style="height:12px;"></div>
                </div>
            </div>
        </div>
        `;
    });

    html += `</div></div>`;
    return html;
}

function toggleFindingCard(card) {
    if (!card) return;
    card.classList.toggle('expanded');
    // Rotation is handled by CSS for more reliable performance
}

function filterAuditFindings(btn, severity) {
    if (!btn) return;
    document.querySelectorAll('.audit-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const wrapper = document.getElementById('audit-findings-list');
    if (!wrapper) return;
    
    const cards = wrapper.querySelectorAll('.finding-card-wrapper');
    cards.forEach(card => {
        if (severity === 'ALL') {
            card.style.display = 'block';
        } else {
            const cardSev = card.dataset.severity || '';
            if (cardSev.toUpperCase().includes(severity.toUpperCase())) {
                card.style.display = 'block';
            } else {
                card.style.display = 'none';
            }
        }
    });
}


// ── Tools view ───────────────────────────────────────────────────────────────
async function renderToolsView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;">
        <h2 style="font-size:18px;font-weight:600;margin-bottom:24px;">Tools & Automation</h2>
        <div class="tools-grid">

        <div class="tool-card">
            <div class="tool-card-header">
            <div class="tool-icon" style="background:var(--accent-dim);color:var(--accent);"><i data-lucide="download" style="width:20px;height:20px;"></i></div>
            <h3>AI Context Export</h3>
            </div>
            <p>Export documentation in a compact, token-efficient format optimized for LLM context windows.</p>
            <div style="margin-bottom:12px;">
            <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:4px;">Format</label>
            <select id="export-format" style="padding:6px 10px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;">
                <option value="markdown">Markdown</option>
                <option value="xml">XML</option>
                <option value="json">JSON</option>
            </select>
            </div>
            <div class="tool-card-actions">
            <button class="tool-btn primary" onclick="toolExport()">Export</button>
            <button class="tool-btn" onclick="toolExportCopy()">Copy to Clipboard</button>
            </div>
            <pre id="export-preview" style="display:none;margin-top:12px;max-height:300px;overflow:auto;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:11px;color:var(--text-muted);white-space:pre-wrap;word-break:break-word;"></pre>
        </div>

        <div class="tool-card">
            <div class="tool-card-header">
            <div class="tool-icon" style="background:rgba(34,197,94,0.1);color:var(--green);"><i data-lucide="filter" style="width:20px;height:20px;"></i></div>
            <h3>Triage Feedback</h3>
            </div>
            <p>Review and manage triage corrections. Flag files that were incorrectly categorized to improve future runs.</p>
            <div class="tool-card-actions">
            <button class="tool-btn primary" onclick="toolLoadTriageFeedback()">View Feedback</button>
            <button class="tool-btn" onclick="toolAddTriageFeedback()">Add Correction</button>
            </div>
            <div id="triage-feedback-list" style="margin-top:12px;"></div>
        </div>

        <div class="tool-card">
            <div class="tool-card-header">
            <div class="tool-icon" style="background:rgba(168,85,247,0.1);color:var(--purple);"><i data-lucide="clock" style="width:20px;height:20px;"></i></div>
            <h3>Scheduled Re-runs</h3>
            </div>
            <p>Configure automatic documentation updates on a cron schedule or triggered by new commits.</p>
            <div class="tool-card-actions">
            <button class="tool-btn" onclick="toolScheduleInfo()">View Schedule Info</button>
            </div>
            <div id="schedule-info" style="margin-top:12px;font-size:12px;color:var(--text-muted);"></div>
        </div>

        <div class="tool-card">
            <div class="tool-card-header">
            <div class="tool-icon" style="background:rgba(245,158,11,0.1);color:var(--orange);"><i data-lucide="eye" style="width:20px;height:20px;"></i></div>
            <h3>Watch Mode</h3>
            </div>
            <p>Monitor file changes and automatically trigger documentation updates on save. Start watch mode from the CLI.</p>
            <div style="padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-family:monospace;font-size:12px;color:var(--text-muted);margin-top:8px;">
            $ codilay watch .
            </div>
        </div>

        <div class="tool-card">
            <div class="tool-card-header">
            <div class="tool-icon" style="background:rgba(59,130,246,0.1);color:var(--accent);"><i data-lucide="network" style="width:20px;height:20px;"></i></div>
            <h3>Graph Filters</h3>
            </div>
            <p>Filter the dependency graph by wire type, file layer, or module. Use the Graph tab's filter panel.</p>
            <div class="tool-card-actions">
            <button class="tool-btn primary" onclick="switchView('graph')">Open Graph View</button>
            </div>
        </div>

        <div class="tool-card">
            <div class="tool-card-header">
            <div class="tool-icon" style="background:rgba(34,197,94,0.1);color:var(--green);"><i data-lucide="git-compare" style="width:20px;height:20px;"></i></div>
            <h3>Doc Diff</h3>
            </div>
            <p>Compare documentation between runs to see what changed at the section level.</p>
            <div class="tool-card-actions">
            <button class="tool-btn primary" onclick="switchView('diff')">Open Diff View</button>
            </div>
        </div>

        </div>
    </div>
    `;
    updateIcons();
}

let lastExportContent = '';

async function toolExport() {
    const fmt = document.getElementById('export-format').value;
    const preview = document.getElementById('export-preview');
    preview.style.display = 'block';
    preview.textContent = 'Exporting...';

    try {
        const res = await fetch(`/api/export?fmt=${encodeURIComponent(fmt)}`);
        if (!res.ok) throw new Error('Export failed');
        const data = await res.json();
        lastExportContent = data.content || '';
        preview.textContent = lastExportContent.slice(0, 5000) + (lastExportContent.length > 5000 ? '\n\n... (truncated, ' + data.chars + ' chars total)' : '');
    } catch (e) {
        preview.textContent = 'Error: ' + e.message;
    }
}

async function toolExportCopy() {
    if (!lastExportContent) await toolExport();
    if (lastExportContent) {
        navigator.clipboard.writeText(lastExportContent).then(() => {
            const btn = document.querySelector('[onclick="toolExportCopy()"]');
            if (btn) { const orig = btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = orig, 2000); }
        });
    }
}

async function toolLoadTriageFeedback() {
    const el = document.getElementById('triage-feedback-list');
    if (!el) return;
    el.innerHTML = '<span style="color:var(--text-muted);font-size:12px;">Loading...</span>';

    try {
        const res = await fetch('/api/triage-feedback');
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        const entries = data.entries || [];

        if (entries.length === 0) {
            el.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">No feedback entries yet.</span>';
            return;
        }

        el.innerHTML = entries.map(e => `
        <div class="triage-item">
        <div>
            <span class="triage-item-file">${escHtml(e.file_path)}</span>
            <span style="font-size:11px;color:var(--text-muted);margin-left:8px;">${escHtml(e.original_category)} &rarr; ${escHtml(e.corrected_category)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
            ${e.reason ? `<span style="font-size:11px;color:var(--text-muted);">${escHtml(e.reason)}</span>` : ''}
            <button class="triage-delete-btn" onclick="toolDeleteTriageFeedback('${escAttr(e.file_path)}')"><i data-lucide="trash-2" style="width:14px;height:14px;"></i></button>
        </div>
        </div>
    `).join('');
        updateIcons();
    } catch (e) {
        el.innerHTML = `<span style="color:var(--red);font-size:12px;">Error: ${escHtml(e.message)}</span>`;
    }
}

async function toolAddTriageFeedback() {
    const file_path = prompt('File path:');
    if (!file_path) return;
    const original_category = prompt('Original category (e.g. skip, include, partial):');
    if (!original_category) return;
    const corrected_category = prompt('Corrected category:');
    if (!corrected_category) return;
    const reason = prompt('Reason (optional):', '') || '';

    try {
        await fetch('/api/triage-feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path, original_category, corrected_category, reason }),
        });
        await toolLoadTriageFeedback();
    } catch (e) { console.error(e); }
}

async function toolDeleteTriageFeedback(filePath) {
    if (!confirm('Remove this feedback?')) return;
    try {
        await fetch(`/api/triage-feedback/${encodeURIComponent(filePath)}`, { method: 'DELETE' });
        await toolLoadTriageFeedback();
    } catch (e) { console.error(e); }
}

function toolScheduleInfo() {
    const el = document.getElementById('schedule-info');
    if (!el) return;
    el.innerHTML = `
    <div style="padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;">
        <p style="margin-bottom:8px;"><strong>CLI Commands:</strong></p>
        <div style="font-family:monospace;font-size:11px;line-height:2;">
        <div>$ codilay schedule set . --cron "0 */6 * * *"</div>
        <div>$ codilay schedule set . --on-commit</div>
        <div>$ codilay schedule status .</div>
        <div>$ codilay schedule remove .</div>
        </div>
        <p style="margin-top:12px;font-size:11px;color:var(--text-muted);">Schedules are managed via the CLI and persist in your project config.</p>
    </div>`;
}

// ── Commit Docs view ─────────────────────────────────────────────────────────
let activeCommitHash = null;

async function renderCommitDocsView() {
    const container = document.getElementById('main-content');
    container.innerHTML = `
    <div style="padding: 32px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 24px;">
            <div>
                <h2 style="font-size:18px;font-weight:600;margin-bottom:8px;">Commit Docs</h2>
                <p style="color:var(--text-muted);font-size:13px;max-width:560px;">
                    Plain-language documentation for each commit — what changed, why each file was touched, and what to watch out for.
                </p>
            </div>
            <div style="display:flex;flex-direction:column;gap:8px;background:var(--bg-secondary);padding:16px;border-radius:8px;border:1px solid var(--border);min-width:280px;">
                <div style="display:flex;gap:8px;align-items:center;">
                    <input id="cd-hash-input" placeholder="Commit hash (blank = latest)" style="flex:1;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;font-family:monospace;">
                    <button class="tool-btn primary" onclick="generateCommitDoc()"><i data-lucide="zap" style="width:14px;height:14px;"></i> Generate</button>
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                    <input id="cd-range-input" placeholder="Range, e.g. main..HEAD" style="flex:1;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;font-family:monospace;">
                    <button class="tool-btn" onclick="generateCommitDocRange()"><i data-lucide="list" style="width:14px;height:14px;"></i> Range</button>
                </div>
                <div style="display:flex;gap:16px;">
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-context-toggle" style="accent-color:var(--accent);"> Include CODEBASE.md context
                    </label>
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-metrics-toggle" style="accent-color:var(--accent);"> Quality metrics
                    </label>
                </div>
            </div>
        </div>

        <div id="cd-status" style="display:none;margin-bottom:16px;padding:12px 16px;border-radius:6px;font-size:13px;"></div>

        <!-- Backfill section -->
        <details id="cd-backfill-section" style="margin-bottom:24px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;">
            <summary style="padding:12px 16px;cursor:pointer;font-size:13px;font-weight:600;list-style:none;display:flex;align-items:center;gap:8px;">
                <i data-lucide="history" style="width:14px;height:14px;color:var(--accent);"></i>
                Backfill History
                <span style="font-size:11px;font-weight:400;color:var(--text-muted);margin-left:4px;">— document existing commits</span>
            </summary>
            <div style="padding:16px;border-top:1px solid var(--border);">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
                    <div>
                        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">From (hash or YYYY-MM-DD)</label>
                        <input id="cd-from-input" placeholder="e.g. abc123f or 2024-01-01" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;font-family:monospace;box-sizing:border-box;">
                    </div>
                    <div>
                        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">To (hash, default: HEAD)</label>
                        <input id="cd-to-input" placeholder="HEAD" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;font-family:monospace;box-sizing:border-box;">
                    </div>
                    <div>
                        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">Last N commits</label>
                        <input id="cd-lastn-input" type="number" min="1" placeholder="e.g. 50" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;box-sizing:border-box;">
                    </div>
                    <div>
                        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">Author filter</label>
                        <input id="cd-author-input" placeholder="name or email" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;box-sizing:border-box;">
                    </div>
                    <div style="grid-column:1/-1;">
                        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">Path filter</label>
                        <input id="cd-path-input" placeholder="e.g. src/payments/" style="width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;box-sizing:border-box;">
                    </div>
                </div>
                <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;">
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-bf-context" style="accent-color:var(--accent);"> CODEBASE.md context
                    </label>
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-bf-metrics" style="accent-color:var(--accent);"> Quality metrics
                    </label>
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-bf-merges" style="accent-color:var(--accent);"> Include merges
                    </label>
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-bf-force" style="accent-color:var(--accent);"> Force re-process all
                    </label>
                    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted);cursor:pointer;">
                        <input type="checkbox" id="cd-bf-force-metrics" style="accent-color:var(--accent);"> Force metrics only
                    </label>
                </div>
                <div style="display:flex;gap:10px;align-items:center;">
                    <button class="tool-btn primary" onclick="previewBackfill()"><i data-lucide="search" style="width:14px;height:14px;"></i> Preview</button>
                    <button class="tool-btn" onclick="startBackfill()"><i data-lucide="play" style="width:14px;height:14px;"></i> Start</button>
                    <span style="font-size:11px;color:var(--text-muted);">For large histories (&gt;200 commits) use the CLI for better progress tracking.</span>
                </div>
                <div id="cd-backfill-preview" style="display:none;margin-top:12px;padding:12px;background:var(--bg);border-radius:6px;border:1px solid var(--border);font-size:12px;"></div>
            </div>
        </details>

        <div id="cd-list-container" style="margin-bottom:24px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h3 style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;">Generated Docs</h3>
                <button class="tool-btn" onclick="viewCommitIndex()" style="font-size:11px;"><i data-lucide="book-open" style="width:12px;height:12px;"></i> Index</button>
            </div>
            <div id="cd-list">Loading...</div>
        </div>

        <div id="cd-detail" style="display:none;background:var(--bg);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
            <div style="padding:12px 16px;background:var(--bg-secondary);border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
                <span id="cd-detail-title" style="font-size:13px;font-weight:600;font-family:monospace;"></span>
                <button class="tool-btn" onclick="closeCommitDoc()" style="font-size:11px;padding:4px 10px;">Close</button>
            </div>
            <div id="cd-detail-content" style="padding:28px 32px;font-size:13px;line-height:1.7;overflow:auto;max-height:60vh;" class="doc-content"></div>
        </div>
    </div>
    `;
    updateIcons();
    loadCommitDocList();
}

async function loadCommitDocList() {
    const listEl = document.getElementById('cd-list');
    if (!listEl) return;
    try {
        const res = await fetch('/api/commit-docs');
        if (!res.ok) throw new Error('Failed to load commit docs');
        const data = await res.json();
        const docs = data.docs || [];

        if (docs.length === 0) {
            listEl.innerHTML = '<div style="font-size:13px;color:var(--text-muted);padding:16px;background:var(--bg-secondary);border-radius:6px;border:1px dashed var(--border);">No commit docs yet. Use the controls above to generate one.</div>';
            return;
        }

        let html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;">';
        docs.forEach(doc => {
            const isActive = doc.hash === activeCommitHash;
            html += `
            <div class="tool-card" style="cursor:pointer;${isActive ? 'border-color:var(--accent);' : ''}" onclick="viewCommitDoc('${escAttr(doc.hash)}')">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
                    <code style="font-size:13px;font-weight:600;color:var(--accent);">${escHtml(doc.hash)}</code>
                    ${doc.date ? `<span style="font-size:10px;color:var(--text-muted);margin-left:8px;white-space:nowrap;">${escHtml(doc.date)}</span>` : ''}
                </div>
                ${doc.message ? `<p style="font-size:12px;color:var(--text-muted);margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(doc.message)}</p>` : ''}
            </div>`;
        });
        html += '</div>';
        listEl.innerHTML = html;
        updateIcons();
    } catch(e) {
        listEl.innerHTML = `<span style="color:var(--red);">${escHtml(e.message)}</span>`;
    }
}

async function viewCommitDoc(hash) {
    activeCommitHash = hash;
    const detailEl = document.getElementById('cd-detail');
    const contentEl = document.getElementById('cd-detail-content');
    const titleEl = document.getElementById('cd-detail-title');

    detailEl.style.display = 'block';
    titleEl.textContent = hash;
    contentEl.innerHTML = '<div class="spinner"></div>';
    detailEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    try {
        const res = await fetch(`/api/commit-docs/${encodeURIComponent(hash)}`);
        if (!res.ok) throw new Error('Failed to load doc');
        const data = await res.json();
        // Strip the embedded JSON comment before passing to markdown renderer
        const mdContent = data.content.replace(/<!--\s*codilay-metrics:[\s\S]*?-->/g, '');
        contentEl.innerHTML = renderMarkdown(mdContent) + renderCommitMetrics(data.content);
        hljs.highlightAll();
        updateIcons();
    } catch(e) {
        contentEl.innerHTML = `<div style="color:var(--red);">${escHtml(e.message)}</div>`;
    }

    // Refresh list to highlight active card
    loadCommitDocList();
}

function closeCommitDoc() {
    activeCommitHash = null;
    const detailEl = document.getElementById('cd-detail');
    if (detailEl) detailEl.style.display = 'none';
    loadCommitDocList();
}

function cdBackfillPayload() {
    return {
        backfill: true,
        from_ref: document.getElementById('cd-from-input').value.trim() || null,
        to_ref: document.getElementById('cd-to-input').value.trim() || 'HEAD',
        last_n: parseInt(document.getElementById('cd-lastn-input').value) || null,
        author: document.getElementById('cd-author-input').value.trim() || null,
        path_filter: document.getElementById('cd-path-input').value.trim() || null,
        include_merges: document.getElementById('cd-bf-merges').checked,
        use_context: document.getElementById('cd-bf-context').checked,
        include_metrics: document.getElementById('cd-bf-metrics').checked,
        force: document.getElementById('cd-bf-force').checked,
        force_metrics: document.getElementById('cd-bf-force-metrics').checked,
        workers: 4,
    };
}

async function previewBackfill() {
    const previewEl = document.getElementById('cd-backfill-preview');
    previewEl.style.display = 'block';
    previewEl.innerHTML = '<span style="color:var(--text-muted);">Estimating…</span>';
    const payload = cdBackfillPayload();
    // Use estimate endpoint — POST with estimate_only flag
    payload.estimate_only = true;
    try {
        const res = await fetch('/api/commit-docs/estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Estimate failed');
        const d = await res.json();
        previewEl.innerHTML = `
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">
                <div><span style="color:var(--text-muted);">Total commits</span><br><strong>${d.total}</strong></div>
                <div><span style="color:var(--text-muted);">Already done</span><br><strong style="color:var(--green);">${d.already_documented}</strong></div>
                <div><span style="color:var(--text-muted);">To process</span><br><strong style="color:var(--accent);">${d.will_process}</strong></div>
            </div>
            <div style="color:var(--orange);">Estimated cost: ~$${d.estimated_cost.toFixed(2)}</div>
        `;
    } catch(e) {
        previewEl.innerHTML = `<span style="color:var(--red);">${escHtml(e.message)}</span>`;
    }
}

async function startBackfill() {
    cdSetStatus('Starting backfill — this may take a while for large histories…', 'info');
    const payload = cdBackfillPayload();
    try {
        const res = await fetch('/api/commit-docs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Backfill failed');
        }
        const d = await res.json();
        const processed = (d.processed || []).length;
        const metricsOnly = (d.metrics_only || []).length;
        const errors = (d.errors || []).length;
        cdSetStatus(
            `Backfill complete — ${processed} processed, ${metricsOnly} metrics-only, ${d.skipped || 0} skipped${errors ? `, ${errors} errors` : ''}`,
            errors ? 'error' : 'success'
        );
        await loadCommitDocList();
    } catch(e) {
        cdSetStatus(e.message, 'error');
    }
}

async function viewCommitIndex() {
    const detailEl = document.getElementById('cd-detail');
    const contentEl = document.getElementById('cd-detail-content');
    const titleEl = document.getElementById('cd-detail-title');
    detailEl.style.display = 'block';
    titleEl.textContent = 'index.md';
    contentEl.innerHTML = '<div class="spinner"></div>';
    detailEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try {
        const res = await fetch('/api/commit-docs/index');
        if (!res.ok) throw new Error('Index not found — generate at least one commit doc first');
        const d = await res.json();
        contentEl.innerHTML = renderMarkdown(d.content);
        updateIcons();
    } catch(e) {
        contentEl.innerHTML = `<div style="color:var(--red);">${escHtml(e.message)}</div>`;
    }
}

function cdSetStatus(msg, type) {
    const el = document.getElementById('cd-status');
    if (!el) return;
    const colors = { info: 'var(--accent)', error: 'var(--red)', success: 'var(--green)' };
    const bgs = { info: 'rgba(59,130,246,.08)', error: 'rgba(239,68,68,.08)', success: 'rgba(34,197,94,.08)' };
    el.style.display = 'block';
    el.style.color = colors[type] || 'var(--text)';
    el.style.background = bgs[type] || 'var(--bg-secondary)';
    el.style.border = `1px solid ${colors[type] || 'var(--border)'}`;
    el.textContent = msg;
}

async function generateCommitDoc() {
    const hash = document.getElementById('cd-hash-input').value.trim() || null;
    const useContext = document.getElementById('cd-context-toggle').checked;
    const includeMetrics = document.getElementById('cd-metrics-toggle').checked;
    cdSetStatus('Generating commit doc…', 'info');
    try {
        const res = await fetch('/api/commit-docs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ commit_hash: hash, use_context: useContext, include_metrics: includeMetrics }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Generation failed');
        }
        const data = await res.json();
        cdSetStatus(`Doc generated for ${data.hash}`, 'success');
        await loadCommitDocList();
        viewCommitDoc(data.hash);
    } catch(e) {
        cdSetStatus(e.message, 'error');
    }
}

async function generateCommitDocRange() {
    const range = document.getElementById('cd-range-input').value.trim();
    if (!range) { cdSetStatus('Enter a commit range first, e.g. main..HEAD', 'error'); return; }
    const useContext = document.getElementById('cd-context-toggle').checked;
    const includeMetrics = document.getElementById('cd-metrics-toggle').checked;
    cdSetStatus(`Generating docs for range "${range}"…`, 'info');
    try {
        const res = await fetch('/api/commit-docs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ commit_range: range, use_context: useContext, include_metrics: includeMetrics }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Generation failed');
        }
        const data = await res.json();
        const count = (data.generated || []).length;
        cdSetStatus(`Generated ${count} commit doc${count !== 1 ? 's' : ''}`, 'success');
        await loadCommitDocList();
        if (data.generated && data.generated.length > 0) {
            viewCommitDoc(data.generated[data.generated.length - 1].hash);
        }
    } catch(e) {
        cdSetStatus(e.message, 'error');
    }
}

function renderCommitMetrics(content) {
    // Extract embedded metrics JSON from HTML comment
    const match = content.match(/<!--\s*codilay-metrics:\s*(\{[\s\S]*?\})\s*-->/);
    if (!match) return '';
    let data;
    try { data = JSON.parse(match[1]); } catch(e) { return ''; }

    const metrics = data.metrics || [];
    const notes = data.reviewer_notes || [];

    function scoreColor(score) {
        if (score === -1) return 'var(--text-muted)';
        if (score >= 8) return 'var(--green)';
        if (score >= 6) return 'var(--orange)';
        return 'var(--red)';
    }
    function scoreBar(score) {
        if (score === -1) return '<span style="font-size:11px;color:var(--text-muted);">N/A</span>';
        const filled = Math.round(score);
        const empty = 10 - filled;
        const color = scoreColor(score);
        return `<span style="color:${color};letter-spacing:1px;">${'█'.repeat(filled)}${'░'.repeat(empty)}</span><span style="font-size:11px;color:${color};margin-left:6px;font-weight:600;">${score}/10</span>`;
    }

    let html = `
    <div style="margin-top:28px;border-top:1px solid var(--border);padding-top:20px;">
        <h3 style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:16px;">Commit Metrics</h3>
        <div style="display:grid;gap:10px;">`;

    for (const m of metrics) {
        html += `
        <div style="display:grid;grid-template-columns:130px 1fr auto;align-items:center;gap:12px;padding:10px 14px;background:var(--bg-secondary);border-radius:6px;border:1px solid var(--border);">
            <span style="font-size:12px;font-weight:500;">${escHtml(m.name)}</span>
            <span style="font-size:12px;color:var(--text-muted);">${escHtml(m.note || '')}</span>
            <div style="white-space:nowrap;">${scoreBar(m.score)}</div>
        </div>`;
    }
    html += '</div>';

    if (notes.length > 0) {
        html += `<div style="margin-top:16px;">
            <div style="font-size:12px;font-weight:600;color:var(--orange);margin-bottom:8px;">Reviewer Notes</div>`;
        for (const note of notes) {
            html += `<div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:6px;font-size:12px;color:var(--text-muted);">
                <span style="color:var(--orange);flex-shrink:0;">⚠</span>
                <span>${escHtml(note)}</span>
            </div>`;
        }
        html += '</div>';
    }
    html += '</div>';
    return html;
}

// ── View switching ───────────────────────────────────────────────────────────
function switchView(view) {
    currentView = view;
    document.querySelectorAll('.view-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.view === view);
    });
    if (view === 'document') renderDocument();
    else if (view === 'graph') renderGraph();
    else if (view === 'diff') renderDiffView();
    else if (view === 'diff-run') renderDiffRunView();
    else if (view === 'search') renderSearchView();
    else if (view === 'team') renderTeamView();
    else if (view === 'audit') renderAuditView();
    else if (view === 'commit-docs') renderCommitDocsView();
    else if (view === 'tools') renderToolsView();
}

document.querySelectorAll('.view-tab').forEach(t => {
    t.addEventListener('click', () => switchView(t.dataset.view));
});

// ── Sidebar tabs ─────────────────────────────────────────────────────────────
document.querySelectorAll('.sidebar-tab').forEach(t => {
    t.addEventListener('click', () => {
        currentTab = t.dataset.tab;
        document.querySelectorAll('.sidebar-tab').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        renderSidebar();
    });
});

document.getElementById('section-search').addEventListener('input', renderSidebar);

// ── Section scrolling ────────────────────────────────────────────────────────
function scrollToSection(id) {
    if (currentView !== 'document') switchView('document');

    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector(`.nav-item[data-id="${id}"]`);
    if (navItem) navItem.classList.add('active');

    const el = document.getElementById(`section-${id}`);
    if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        // Flash highlight
        el.style.borderColor = 'var(--accent)';
        el.style.boxShadow = '0 0 0 1px var(--accent)';
        setTimeout(() => {
            el.style.borderColor = '';
            el.style.boxShadow = '';
        }, 2000);
    }
}

// ── File viewer ──────────────────────────────────────────────────────────────
async function openFile(path) {
    const overlay = document.getElementById('file-viewer-overlay');
    const title = document.getElementById('file-viewer-title');
    const code = document.getElementById('file-viewer-code');

    title.textContent = path;
    code.textContent = 'Loading...';

    // Reset Highlight.js state before loading new file
    code.className = '';
    delete code.dataset.highlighted;

    overlay.classList.remove('hidden');

    try {
        const res = await fetch(`/api/file/${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = await res.json();

        // Inject the raw text
        code.textContent = data.content;

        // Try to guess the language from the file extension
        const ext = path.includes('.') ? path.split('.').pop().toLowerCase() : 'plaintext';

        // Map common extensions to highlight.js language aliases
        const extMap = {
            'js': 'javascript', 'jsx': 'javascript', 'ts': 'typescript', 'tsx': 'typescript',
            'py': 'python', 'rb': 'ruby', 'go': 'go', 'rs': 'rust', 'html': 'html',
            'css': 'css', 'json': 'json', 'md': 'markdown', 'yml': 'yaml',
            'yaml': 'yaml', 'sh': 'bash', 'c': 'c', 'cpp': 'cpp', 'h': 'c',
            'java': 'java', 'xml': 'xml', 'sql': 'sql', 'php': 'php'
        };
        const lang = extMap[ext] || ext;

        // Apply the syntax highlighting
        code.classList.add(`language-${lang}`);
        hljs.highlightElement(code);

    } catch (err) {
        code.textContent = `Could not load file: ${err.message}`;
        code.className = 'language-plaintext';
    }
}

function closeFileViewer(event) {
    if (event && event.target !== document.getElementById('file-viewer-overlay')) return;
    document.getElementById('file-viewer-overlay').classList.add('hidden');
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeFileViewer();
});

// ── Chat ─────────────────────────────────────────────────────────────────────
let currentConvId = null;
let currentActiveBranchId = 'main';
let currentUser = localStorage.getItem('codilay-user') || null;
let deepMode = false;
let historyOpen = false;
let branchesOpen = false;
let chatWidth = parseInt(localStorage.getItem('chatWidth')) || 440;

// Initialize chat width
if (chatOpen) {
    const panel = document.getElementById('chat-panel');
    panel.style.width = chatWidth + 'px';
    panel.style.minWidth = chatWidth + 'px';
}

function toggleChat() {
    chatOpen = !chatOpen;
    const panel = document.getElementById('chat-panel');
    panel.classList.toggle('collapsed', !chatOpen);
    document.getElementById('chat-toggle').classList.toggle('hidden', chatOpen);

    if (chatOpen) {
        panel.style.width = chatWidth + 'px';
        panel.style.minWidth = chatWidth + 'px';
        document.getElementById('chat-input').focus();
        chatLoadMemoryBar();
    } else {
        panel.style.width = '0';
        panel.style.minWidth = '0';
    }
}

// ── Resizing Logic ───────────────────────────────
const resizer = document.getElementById('chat-resizer');
const chatPanel = document.getElementById('chat-panel');
let isResizing = false;

resizer.addEventListener('mousedown', (e) => {
    isResizing = true;
    resizer.classList.add('active');
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
});

document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;

    const width = window.innerWidth - e.clientX;
    if (width >= 320 && width <= 800) {
        chatWidth = width;
        chatPanel.style.width = width + 'px';
        chatPanel.style.minWidth = width + 'px';
        localStorage.setItem('chatWidth', width);
    }
});

document.addEventListener('mouseup', () => {
    if (isResizing) {
        isResizing = false;
        resizer.classList.remove('active');
        document.body.style.cursor = '';
    }
});

// Auto-grow textarea
const chatInput = document.getElementById('chat-input');
chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + 'px';
});

chatInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
    }
});

// ── Deep mode toggle ─────────────────────────────
function chatToggleDeep() {
    deepMode = !deepMode;
    document.getElementById('deep-mode-btn').classList.toggle('active', deepMode);
}

// ── Suggestion cards ─────────────────────────────
function chatAskSuggestion(el) {
    const text = el.innerText.trim();
    document.getElementById('chat-input').value = text;
    sendChat();
}

// ── Send message ─────────────────────────────────
async function sendChat() {
    const input = document.getElementById('chat-input');
    const question = input.value.trim();
    if (!question) return;

    // Add user message (msg ID not yet known; will be set when conversation is resumed)
    addChatMsg(question, 'user');
    input.value = '';
    input.style.height = 'auto';
    input.disabled = true;
    document.getElementById('chat-send').disabled = true;

    // Show typing indicator
    const loadingId = showTypingIndicator();

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question,
                deep: deepMode,
                conversation_id: currentConvId || undefined,
            }),
        });

        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = await res.json();

        // Track conversation
        if (data.conversation_id) {
            currentConvId = data.conversation_id;
        }

        // Remove typing indicator
        removeChatMsg(loadingId);

        // Build answer with new structure
        addBotMessage(data);

        // Update conversation title
        const titleEl = document.getElementById('chat-conv-title');
        if (titleEl.textContent === 'New conversation') {
            titleEl.textContent = question.length > 40 ? question.slice(0, 40) + '…' : question;
        }

        // Reset deep mode after use
        if (deepMode) {
            deepMode = false;
            document.getElementById('deep-mode-btn').classList.remove('active');
        }

    } catch (err) {
        removeChatMsg(loadingId);
        addChatMsg(`Error: ${err.message}`, 'bot');
    }

    input.disabled = false;
    document.getElementById('chat-send').disabled = false;
    input.focus();
}

// ── Add bot message with full structure ───────────
function addBotMessage(data) {
    const messages = document.getElementById('chat-messages');
    const welcome = messages.querySelector('.welcome-msg');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = 'chat-msg bot';
    div.dataset.msgId = data.message_id || '';

    let html = `<div class="msg-label"><i data-lucide="bot"></i> CodiLay</div>`;
    html += `<div class="msg-bubble">`;
    html += `<div class="msg-content">${renderMarkdown(data.answer)}</div>`;

    // Meta section: badges + sources
    const hasMeta = data.escalated || (data.sources && data.sources.length > 0);
    if (hasMeta) {
        html += `<div class="msg-meta">`;
        if (data.escalated) {
            html += `<span class="msg-badge deep"><i data-lucide="search" style="width: 12px; height: 12px;"></i> Deep Agent</span>`;
        }
        if (data.sources && data.sources.length > 0) {
            data.sources.forEach(s => {
                const sec = sections.find(x => x.id === s);
                if (sec) {
                    html += `<span class="msg-badge source" onclick="scrollToSection('${s}')">${escHtml(sec.title)}</span>`;
                } else {
                    html += `<span class="msg-badge source" onclick="openFile('${escAttr(s)}')">${escHtml(s)}</span>`;
                }
            });
        }
        html += `</div>`;
    }

    html += `</div>`; // close msg-bubble

    // Action buttons (shown on hover)
    html += `<div class="msg-actions">`;
    html += `<button class="msg-action-btn" onclick="chatCopyMsg(this)" title="Copy"><i data-lucide="copy" style="width: 12px; height: 12px;"></i> Copy</button>`;
    if (data.message_id && currentConvId) {
        html += `<button class="msg-action-btn" onclick="chatPinMsg('${data.message_id}')" title="Pin"><i data-lucide="pin" style="width: 12px; height: 12px;"></i> Pin</button>`;
        html += `<button class="msg-action-btn" onclick="chatPromoteMsg('${data.message_id}')" title="Promote to docs"><i data-lucide="file-up" style="width: 12px; height: 12px;"></i> Promote</button>`;
    }
    html += `</div>`;

    div.innerHTML = html;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    updateIcons();
}

// ── Add simple message ───────────────────────────
let msgCounter = 0;

function addChatMsg(content, role, isHtml = false, msgId = null) {
    const id = `msg-${++msgCounter}`;
    const messages = document.getElementById('chat-messages');
    const welcome = messages.querySelector('.welcome-msg');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `chat-msg ${role}`;
    div.id = id;
    if (msgId) div.dataset.msgId = msgId;

    if (role === 'user') {
        const editBtn = msgId && currentConvId
            ? `<button class="msg-action-btn" onclick="chatStartEdit('${msgId}', this)" title="Edit — creates new branch"><i data-lucide="pencil" style="width:12px;height:12px;"></i> Edit</button>`
            : '';
        div.innerHTML = `
            <div class="msg-label"><i data-lucide="user"></i> ${escHtml(currentUser || 'You')}</div>
            <div class="msg-bubble">${escHtml(content)}</div>
            ${editBtn ? `<div class="msg-actions">${editBtn}</div>` : ''}`;
    } else if (isHtml) {
        div.innerHTML = content;
    } else {
        div.innerHTML = `<div class="msg-label"><i data-lucide="bot"></i> CodiLay</div><div class="msg-bubble"><div class="msg-content">${renderMarkdown(content)}</div></div>`;
    }

    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    updateIcons();
    return id;
}

// ── Edit message → branch ─────────────────────────
function chatStartEdit(msgId, btn) {
    const msgDiv = btn.closest('.chat-msg');
    const bubble = msgDiv.querySelector('.msg-bubble');
    const originalText = bubble.textContent.trim();

    bubble.innerHTML = `
        <textarea class="msg-edit-input" rows="3" style="width:100%;resize:vertical;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--accent);border-radius:6px;padding:8px;font-size:13px;">${escHtml(originalText)}</textarea>
        <div style="display:flex;gap:6px;margin-top:6px;">
            <button class="tool-btn primary" style="font-size:12px;padding:4px 10px;" onclick="chatSubmitEdit('${msgId}', this)">Send (new branch)</button>
            <button class="tool-btn" style="font-size:12px;padding:4px 10px;" onclick="chatCancelEdit(this, '${escAttr(originalText)}')">Cancel</button>
        </div>`;
    bubble.querySelector('textarea').focus();
}

function chatCancelEdit(btn, originalText) {
    const bubble = btn.closest('.msg-bubble');
    bubble.innerHTML = escHtml(originalText);
}

async function chatSubmitEdit(msgId, btn) {
    const bubble = btn.closest('.msg-bubble');
    const textarea = bubble.querySelector('textarea');
    const newContent = textarea.value.trim();
    if (!newContent || !currentConvId) return;

    btn.disabled = true;
    btn.textContent = 'Creating branch…';

    try {
        const params = new URLSearchParams({ content: newContent });
        const res = await fetch(`/api/conversations/${currentConvId}/messages/${msgId}/edit?${params}`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error(await res.text());
        const conv = await res.json();

        currentActiveBranchId = conv.active_branch_id || 'main';

        // Re-render the conversation with the new active branch
        await chatResumeConv(currentConvId);
        updateBranchIndicator();
    } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Send (new branch)';
        alert(`Edit failed: ${e.message}`);
    }
}

// ── Branch switcher ───────────────────────────────
async function chatToggleBranches() {
    if (!currentConvId) return;
    branchesOpen = !branchesOpen;
    const panel = document.getElementById('chat-branch-panel');
    if (!panel) return;
    panel.classList.toggle('open', branchesOpen);

    if (branchesOpen) {
        try {
            const res = await fetch(`/api/conversations/${currentConvId}/branches`);
            const data = await res.json();
            const branches = data.branches || [];

            if (branches.length <= 1) {
                panel.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:12px;">Only one branch — edit a message to create more.</div>';
            } else {
                panel.innerHTML = branches.map(b => `
                    <div class="branch-item ${b.is_active ? 'active' : ''}" onclick="chatSwitchBranch('${b.id}')">
                        <div class="branch-item-label">
                            <i data-lucide="${b.is_active ? 'git-branch' : 'git-branch'}" style="width:12px;height:12px;${b.is_active ? 'color:var(--accent);' : ''}"></i>
                            ${b.is_active ? '<strong>' : ''}${escHtml(b.label)}${b.is_active ? '</strong>' : ''}
                            ${b.is_active ? '<span style="font-size:10px;color:var(--accent);margin-left:4px;">● active</span>' : ''}
                        </div>
                        <div class="branch-item-meta">${b.message_count} msgs · forked after msg ${b.fork_msg_id ? b.fork_msg_id.slice(0, 6) : 'root'}</div>
                    </div>`).join('');
            }
            updateIcons();
        } catch (e) {
            panel.innerHTML = '<div style="padding:12px;color:var(--red);font-size:12px;">Failed to load branches</div>';
        }
    }
}

async function chatSwitchBranch(branchId) {
    if (!currentConvId) return;
    try {
        const res = await fetch(`/api/conversations/${currentConvId}/branches/switch/${branchId}`, { method: 'POST' });
        if (!res.ok) throw new Error(await res.text());
        const conv = await res.json();
        currentActiveBranchId = conv.active_branch_id || branchId;
        branchesOpen = false;
        document.getElementById('chat-branch-panel').classList.remove('open');
        await chatResumeConv(currentConvId);
        updateBranchIndicator();
    } catch (e) {
        console.error('Switch branch failed:', e);
    }
}

async function updateBranchIndicator() {
    if (!currentConvId) return;
    try {
        const res = await fetch(`/api/conversations/${currentConvId}/branches`);
        const data = await res.json();
        const branches = data.branches || [];
        const btn = document.getElementById('chat-branch-btn');
        if (btn) {
            const active = branches.find(b => b.is_active);
            const label = active ? active.label : 'main';
            btn.innerHTML = `<i data-lucide="git-branch"></i> ${escHtml(label)}${branches.length > 1 ? ` <span style="background:var(--accent);color:#fff;border-radius:8px;padding:1px 5px;font-size:10px;">${branches.length}</span>` : ''}`;
            btn.style.display = branches.length > 0 ? '' : 'none';
            updateIcons();
        }
    } catch (e) { /* ignore */ }
}

function removeChatMsg(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// ── Typing indicator ─────────────────────────────
function showTypingIndicator() {
    const id = `msg-${++msgCounter}`;
    const messages = document.getElementById('chat-messages');

    const div = document.createElement('div');
    div.className = 'chat-msg bot';
    div.id = id;
    div.innerHTML = `
    <div class="msg-label"><i data-lucide="bot"></i> CodiLay</div>
    <div class="msg-bubble">
        <div class="typing-indicator">
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        <div class="typing-dot"></div>
        </div>
    </div>
    `;

    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    updateIcons();
    return id;
}

// ── Message actions ──────────────────────────────
function chatCopyMsg(btn) {
    const bubble = btn.closest('.chat-msg').querySelector('.msg-content');
    if (bubble) {
        navigator.clipboard.writeText(bubble.innerText).then(() => {
            const orig = btn.innerHTML;
            btn.innerHTML = '<i data-lucide="check" style="width: 12px; height: 12px; color: var(--green);"></i> Copied';
            updateIcons();
            setTimeout(() => { btn.innerHTML = orig; updateIcons(); }, 1500);
        });
    }
}

async function chatPinMsg(msgId) {
    if (!currentConvId) return;
    const btn = window.event ? window.event.target.closest('.msg-action-btn') : null;
    try {
        await fetch(`/api/conversations/${currentConvId}/messages/${msgId}/pin`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
        });
        if (btn) {
            btn.innerHTML = '<i data-lucide="pin" style="width: 12px; height: 12px; color: var(--orange);"></i> Pinned';
            updateIcons();
        }
    } catch (e) { console.error('Pin failed:', e); }
}

async function chatPromoteMsg(msgId) {
    if (!currentConvId) return;
    try {
        const res = await fetch(`/api/conversations/${currentConvId}/messages/${msgId}/promote`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
        });
        const data = await res.json();
        if (data.promoted) {
            addChatMsg(`Promoted to documentation section: **${data.section_id}**`, 'bot');
        } else {
            addChatMsg('Could not promote this message.', 'bot');
        }
    } catch (e) { addChatMsg(`Promotion failed: ${e.message}`, 'bot'); }
}

// ── Conversation management ──────────────────────
async function chatNewConversation() {
    currentConvId = null;
    document.getElementById('chat-conv-title').textContent = 'New conversation';
    const messages = document.getElementById('chat-messages');
    messages.innerHTML = `
    <div class="welcome-msg">
        <span class="welcome-icon"><i data-lucide="message-square-more"></i></span>
        <strong>Codebase Assistant</strong>
        Ask anything about this codebase.<br><br>
        <div class="welcome-suggestions">
        <div class="welcome-suggestion" onclick="chatAskSuggestion(this)">
            <i data-lucide="shield-check"></i> How is authentication handled?
        </div>
        <div class="welcome-suggestion" onclick="chatAskSuggestion(this)">
            <i data-lucide="layout-template"></i> What's the project architecture?
        </div>
        <div class="welcome-suggestion" onclick="chatAskSuggestion(this)">
            <i data-lucide="door-open"></i> What are the main entry points?
        </div>
        </div>
    </div>
    `;
    updateIcons();
}

async function chatToggleHistory() {
    const dropdown = document.getElementById('chat-conv-dropdown');
    historyOpen = !historyOpen;
    dropdown.classList.toggle('open', historyOpen);

    if (historyOpen) {
        try {
            // Build user-aware URL for privacy filtering
            const params = new URLSearchParams();
            if (currentUser) params.set('user', currentUser);
            const res = await fetch('/api/conversations?' + params);
            const data = await res.json();
            const convs = data.conversations || [];

            const userLabel = currentUser
                ? `<div class="conv-user-row"><i data-lucide="user" style="width:12px;height:12px;"></i> <span>${escHtml(currentUser)}</span> <button class="conv-change-user-btn" onclick="chatSetUser()" title="Change user">change</button></div>`
                : `<div class="conv-user-row"><button class="conv-change-user-btn" onclick="chatSetUser()"><i data-lucide="user" style="width:12px;height:12px;"></i> Set username</button></div>`;

            if (convs.length === 0) {
                dropdown.innerHTML = userLabel + '<div style="padding:12px 16px;color:var(--text-muted);font-size:12px;text-align:center;">No past conversations</div>';
            } else {
                const privateConvs = convs.filter(c => c.visibility === 'private');
                const teamConvs = convs.filter(c => c.visibility === 'team');

                const convItem = c => `
                    <div class="chat-conv-item" onclick="chatResumeConv('${c.id}')">
                    <div class="chat-conv-item-title">${escHtml(c.title || 'Untitled')}</div>
                    <div class="chat-conv-item-meta">
                        <span>${c.message_count || 0} msgs</span>
                        ${c.branch_count > 1 ? `<span>&bull;</span><span><i data-lucide="git-branch" style="width:10px;height:10px;"></i> ${c.branch_count} branches</span>` : ''}
                        <span>&bull;</span>
                        <span>${(c.updated_at || '').slice(0, 16)}</span>
                    </div>
                    </div>`;

                let html = userLabel;
                if (privateConvs.length > 0) {
                    html += `<div class="conv-section-label"><i data-lucide="lock" style="width:11px;height:11px;"></i> Private</div>`;
                    html += privateConvs.map(convItem).join('');
                }
                if (teamConvs.length > 0) {
                    html += `<div class="conv-section-label"><i data-lucide="users" style="width:11px;height:11px;"></i> Team</div>`;
                    html += teamConvs.map(convItem).join('');
                }
                dropdown.innerHTML = html;
            }
            updateIcons();
        } catch (e) {
            dropdown.innerHTML = '<div style="padding:16px;color:var(--red);font-size:12px;">Failed to load history</div>';
        }
    }
}

function chatSetUser() {
    const name = prompt('Enter your username (leave blank to clear):', currentUser || '');
    if (name === null) return; // cancelled
    currentUser = name.trim() || null;
    if (currentUser) {
        localStorage.setItem('codilay-user', currentUser);
    } else {
        localStorage.removeItem('codilay-user');
    }
    // Refresh the dropdown if open
    if (historyOpen) chatToggleHistory();
}

async function chatNewConversationWithVisibility() {
    const vis = prompt('Visibility for new conversation:\n  private — only visible to you\n  team    — shared with team\n\nEnter "private" or "team":', 'private');
    if (!vis) return;
    const visibility = vis.trim().toLowerCase() === 'team' ? 'team' : 'private';
    const params = new URLSearchParams({ visibility });
    if (currentUser) params.set('owner', currentUser);
    try {
        const res = await fetch('/api/conversations?' + params, { method: 'POST' });
        const conv = await res.json();
        currentConvId = conv.id;
        currentActiveBranchId = conv.active_branch_id || 'main';
        document.getElementById('chat-conv-title').textContent = 'New conversation';
        document.getElementById('chat-messages').innerHTML = '';
        chatNewConversation();
    } catch (e) {
        console.error('Failed to create conversation:', e);
        chatNewConversation(); // fallback
    }
}

async function chatResumeConv(convId) {
    historyOpen = false;
    branchesOpen = false;
    document.getElementById('chat-conv-dropdown').classList.remove('open');
    const branchPanel = document.getElementById('chat-branch-panel');
    if (branchPanel) branchPanel.classList.remove('open');

    try {
        const res = await fetch(`/api/conversations/${convId}`);
        const conv = await res.json();
        currentConvId = conv.id;
        currentActiveBranchId = conv.active_branch_id || 'main';
        document.getElementById('chat-conv-title').textContent = conv.title || 'Untitled';

        // Render existing messages
        const messages = document.getElementById('chat-messages');
        messages.innerHTML = '';

        (conv.messages || []).forEach(m => {
            if (m.role === 'user') {
                addChatMsg(m.content, 'user', false, m.id);  // pass msg.id for edit support
            } else if (m.role === 'assistant') {
                addBotMessage({
                    answer: m.content,
                    sources: m.sources || [],
                    escalated: m.escalated || false,
                    message_id: m.id,
                    conversation_id: convId,
                });
            }
        });

        messages.scrollTop = messages.scrollHeight;
        updateBranchIndicator();
    } catch (e) {
        console.error('Resume failed:', e);
    }
}

async function chatExport() {
    if (!currentConvId) return;
    try {
        const res = await fetch(`/api/conversations/${currentConvId}/export`);
        const data = await res.json();
        if (data.markdown) {
            const blob = new Blob([data.markdown], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'conversation.md';
            a.click();
            URL.revokeObjectURL(url);
        }
    } catch (e) { console.error('Export failed:', e); }
}

// ── Memory ───────────────────────────────────────
async function chatLoadMemoryBar() {
    try {
        const res = await fetch('/api/memory');
        const mem = await res.json();
        const facts = (mem.facts || []).length;
        const prefs = Object.keys(mem.preferences || {}).length;
        const bar = document.getElementById('chat-memory-bar');

        if (facts > 0 || prefs > 0) {
            bar.style.display = 'flex';
            document.getElementById('chat-memory-text').textContent =
                `Memory: ${facts} facts, ${prefs} preferences`;
        } else {
            bar.style.display = 'none';
        }
    } catch (e) { /* ignore */ }
}

async function chatShowMemory() {
    try {
        const res = await fetch('/api/memory');
        const mem = await res.json();
        const facts = mem.facts || [];
        const prefs = mem.preferences || {};
        const topics = mem.frequent_topics || {};

        let md = '### Stored Memory\n\n';
        if (facts.length > 0) {
            md += '**Facts:**\n';
            facts.forEach(f => { md += `- [${f.category || 'general'}] ${f.fact}\n`; });
        }
        if (Object.keys(prefs).length > 0) {
            md += '\n**Preferences:**\n';
            Object.entries(prefs).forEach(([k, v]) => { md += `- ${k}: ${v}\n`; });
        }
        if (Object.keys(topics).length > 0) {
            md += '\n**Frequent Topics:**\n';
            Object.entries(topics).sort((a, b) => b[1] - a[1]).slice(0, 10)
                .forEach(([t, c]) => { md += `- ${t} (${c}×)\n`; });
        }
        if (facts.length === 0 && Object.keys(prefs).length === 0) {
            md = 'Memory is empty. Chat more to build up context!';
        }
        addChatMsg(md, 'bot');
    } catch (e) { addChatMsg('Could not load memory.', 'bot'); }
}

// Close dropdown on outside click
document.addEventListener('click', e => {
    const dropdown = document.getElementById('chat-conv-dropdown');
    if (historyOpen && !dropdown.contains(e.target) && !e.target.closest('.chat-icon-btn')) {
        historyOpen = false;
        dropdown.classList.remove('open');
    }
});

// ── Helpers ──────────────────────────────────────────────────────────────────
function renderMarkdown(text) {
    if (!text) return '';
    try {
        const html = marked.parse(text);

        // Post-process to add copy buttons and language labels
        const div = document.createElement('div');
        div.innerHTML = html;

        // Add copy buttons to code blocks
        div.querySelectorAll('pre').forEach(pre => {
            const code = pre.querySelector('code');
            const lang = (code.className.match(/language-(\w+)/) || [])[1] || 'text';
            pre.setAttribute('data-lang', lang);

            const btn = document.createElement('button');
            btn.className = 'code-copy-btn';
            btn.innerHTML = '<i data-lucide="copy" style="width: 14px; height: 14px;"></i>';
            btn.onclick = () => chatCopyCode(btn);
            pre.appendChild(btn);
        });

        // Auto-link file paths and section IDs in inline code
        div.querySelectorAll('code').forEach(el => {
            if (el.parentElement.tagName === 'PRE') return;
            const text = el.textContent.trim();

            // Check if it's a section ID
            const isSection = sections.some(s => s.id === text);
            if (isSection) {
                el.style.color = 'var(--accent)';
                el.style.cursor = 'pointer';
                el.style.textDecoration = 'underline';
                el.addEventListener('click', () => scrollToSection(text));
                return;
            }

            // Check if it's a file path
            const isFile = sections.some(s => s.file === text);
            if (isFile) {
                el.style.color = 'var(--purple)';
                el.style.cursor = 'pointer';
                el.style.textDecoration = 'underline';
                el.addEventListener('click', () => openFile(text));
            }
        });

        return div.innerHTML;
    } catch {
        return escHtml(text);
    }
}

// Configure marked with highlight.js
marked.setOptions({
    highlight: function (code, lang) {
        const language = hljs.getLanguage(lang) ? lang : 'plaintext';
        return hljs.highlight(code, { language }).value;
    },
    langPrefix: 'hljs language-'
});

function chatCopyCode(btn) {
    const pre = btn.closest('pre');
    const code = pre.querySelector('code').innerText;
    navigator.clipboard.writeText(code).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '<i data-lucide="check" style="width: 14px; height: 14px; color: var(--green);"></i>';
        updateIcons();
        setTimeout(() => { btn.innerHTML = orig; updateIcons(); }, 2000);
    });
}

function escHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function escAttr(s) {
    return s.replace(/'/g, "\\'").replace(/"/g, '\\"');
}

// ── Scroll spy ───────────────────────────────────────────────────────────────
const mainContent = document.getElementById('main-content');
mainContent.addEventListener('scroll', () => {
    if (currentView !== 'document') return;

    const cards = mainContent.querySelectorAll('.section-card');
    let activeId = null;

    for (const card of cards) {
        const rect = card.getBoundingClientRect();
        const containerRect = mainContent.getBoundingClientRect();
        if (rect.top <= containerRect.top + 100) {
            activeId = card.id.replace('section-', '');
        }
    }

    if (activeId) {
        document.querySelectorAll('.nav-item').forEach(n => {
            n.classList.toggle('active', n.dataset.id === activeId);
        });
    }
});

// ── Boot ─────────────────────────────────────────────────────────────────────
window.onload = () => {
    updateIcons();
    init();
};
