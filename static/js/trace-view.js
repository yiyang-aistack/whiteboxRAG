/**
 * static/index.html - trace panel view layer
 *
 * Content: sidebar and panel switching, the business summary view (modules 1-5: overview / recall
 *       summary / sentence tracing / diagnostics / quality metrics), the modal centering and
 *       drag-resize helpers, the sentence source modal (showSentenceSource) and the panel reset.
 * Split out of index.html's inline <script>.
 */

// ==================== Sidebar and panel switching ====================
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('-translate-x-full');
    sidebar.classList.toggle('translate-x-0');
}

function toggleSourcePanel() {
    const panel = document.getElementById('sourcePanel');
    panel.classList.toggle('hidden');
    // Remember a manual close so the automatic expand does not fight the user's intent
    state.panelUserClosed = panel.classList.contains('hidden');
}

// ==================== Trace view switching ====================
function switchTraceView(mode) {
    state.traceViewMode = mode;
    const summaryView = document.getElementById('summaryView');
    const techView = document.getElementById('techView');
    const tabSummary = document.getElementById('tabSummary');
    const tabTech = document.getElementById('tabTech');
    const activeCls = ['bg-white', 'text-blue-600', 'shadow-sm'];
    const inactiveCls = ['text-slate-500'];

    if (mode === 'summary') {
        summaryView.classList.remove('hidden');
        techView.classList.add('hidden');
        tabSummary.classList.add(...activeCls);
        tabSummary.classList.remove(...inactiveCls);
        tabTech.classList.remove(...activeCls);
        tabTech.classList.add(...inactiveCls);
    } else {
        summaryView.classList.add('hidden');
        techView.classList.remove('hidden');
        tabTech.classList.add(...activeCls);
        tabTech.classList.remove(...inactiveCls);
        tabSummary.classList.remove(...activeCls);
        tabSummary.classList.add(...inactiveCls);
    }
}

// Business summary view: derive the conclusive display from state.traceData
// Business summary view: 5 modules, conclusion first, risks before hidden technical details
function updateSummaryView() {
    const td = state.traceData;
    // The overall risk level (green / yellow / red) is decided by the drift rate + the overall score
    let riskLevel = 'green', riskLabel = t('trace.risk_normal'), riskBg = 'bg-green-100', riskColor = 'text-green-700', riskBorder = 'border-green-200';
    if (td.driftAnalysis) {
        const dr = td.driftAnalysis.drift_rate || 0;
        if (dr > 0.3) { riskLevel = 'red'; riskLabel = t('trace.risk_label_hallucination'); riskBg = 'bg-red-100'; riskColor = 'text-red-700'; riskBorder = 'border-red-200'; }
        else if (dr > 0.1) { riskLevel = 'yellow'; riskLabel = t('trace.risk_label_low'); riskBg = 'bg-yellow-100'; riskColor = 'text-yellow-700'; riskBorder = 'border-yellow-200'; }
    }
    if (riskLevel === 'green' && td.evaluation && (td.evaluation.overall_score || 0) < 0.6) {
        riskLevel = 'yellow'; riskLabel = t('trace.risk_label_low'); riskBg = 'bg-yellow-100'; riskColor = 'text-yellow-700'; riskBorder = 'border-yellow-200';
    }

    // ==================== Module 1 | Overview of this Q&A ====================
    const oEl = document.getElementById('summaryOverviewContent');
    if (td.userQuery || td.aiAnswer || td.evaluation || td.intent) {
        let isLowQuality = td.retrievalQuality === 'low';
        const avgScore = td.avgRetrievalScore || 0;
        // No retrieval result at all (as opposed to "low relevance"): results === null means not
        // fetched yet, an empty array means nothing matched
        const noResult = Array.isArray(td.results) && td.results.length === 0;
        const hasRetrieved = td.results !== null;  // Whether the retrieval event has arrived

        // ===== Fallback rule: even when the backend marks the result as high, an evaluation score < 0.4 also counts as a weak match =====
        // (guards against inflated retrieval scores with completely irrelevant content)
        const overScore = td.evaluation ? (td.evaluation.overall_score || 0) : 0;
        if (!isLowQuality && !noResult && hasRetrieved && td.evaluation && overScore < 0.4) {
            isLowQuality = true;
        }

        // ===== Conclusion card: the first thing on screen - the white-box RAG core: tell the user the knowledge base match status straight away =====
        let matchIcon, matchText, matchColor, matchBg, matchBorder, handleText;
        if (!hasRetrieved) {
            // No retrieval event yet, show the placeholder state
            matchIcon = '⏳';
            matchText = t('trace.match_pending');
            matchColor = 'text-slate-500';
            matchBg = 'bg-slate-50';
            matchBorder = 'border-slate-200';
            handleText = t('trace.handle_matching');
        } else if (noResult) {
            // No document matched at all -> the clearest "not in the knowledge base"
            matchIcon = '❌';
            matchText = t('trace.match_none_short');
            matchColor = 'text-red-700';
            matchBg = 'bg-red-50';
            matchBorder = 'border-red-200';
            handleText = t('trace.process_llm');
        } else if (isLowQuality) {
            // Documents matched but the average score is below 0.4 -> weak relevance
            matchIcon = '⚠️';
            matchText = t('trace.match_low_avg').replace('{v}', fmtScore(avgScore));
            matchColor = 'text-amber-700';
            matchBg = 'bg-amber-50';
            matchBorder = 'border-amber-200';
            handleText = t('trace.process_llm');
        } else {
            // Retrieval results with an average score above 0.4 -> normal match
            matchIcon = '✅';
            matchText = t('trace.match_good_avg').replace('{v}', fmtScore(avgScore));
            matchColor = 'text-green-700';
            matchBg = 'bg-green-50';
            matchBorder = 'border-green-200';
            handleText = t('trace.process_kb');
        }
        const conclusionCard = `
            <div class="${matchBg} border ${matchBorder} rounded-lg px-3 py-2.5">
                <div class="text-[10px] font-semibold text-slate-500 mb-1.5">📋 ${escapeHtml(t('trace.conclusion'))}</div>
                <div class="space-y-1">
                    <div class="flex items-start gap-1.5 text-xs">
                        <span class="text-slate-500 shrink-0">${escapeHtml(t('trace.query_label'))}</span>
                        <span class="text-slate-700 font-medium break-words">${escapeHtml(td.userQuery || t('trace.query_none'))}</span>
                    </div>
                    <div class="flex items-center gap-1.5 text-xs">
                        <span class="text-slate-500 shrink-0">${escapeHtml(t('trace.match_status'))}</span>
                        <span class="font-bold ${matchColor}">${matchIcon} ${escapeHtml(matchText)}</span>
                    </div>
                    <div class="flex items-center gap-1.5 text-xs">
                        <span class="text-slate-500 shrink-0">${escapeHtml(t('trace.process_method'))}</span>
                        <span class="text-slate-600">${escapeHtml(handleText)}</span>
                    </div>
                </div>
            </div>`;

        // One-line summary
        let oneLine = t('trace.one_line_normal');
        if (!hasRetrieved) {
            oneLine = t('trace.one_line_retrieving');
        } else if (noResult) {
            oneLine = t('trace.one_line_no_result');
        } else if (isLowQuality) {
            oneLine = t('trace.one_line_low_quality');
        } else if (riskLevel === 'red') {
            const s = td.evaluation ? Math.round((td.evaluation.overall_score || 0) * 100) : '-';
            oneLine = t('trace.one_line_hallucination').replace('{s}', s);
        } else if (riskLevel === 'yellow') {
            const s = td.evaluation ? Math.round((td.evaluation.overall_score || 0) * 100) : '-';
            oneLine = t('trace.one_line_low_conf').replace('{s}', s);
        } else if (td.evaluation) {
            oneLine = t('trace.one_line_scored').replace('{s}', Math.round((td.evaluation.overall_score || 0) * 100));
        }

        // Business intent
        let intentHtml = '';
        if (td.intent) {
            const intentType = td.intent.intent_type || t('trace.intent_unknown');
            const confPct = Math.round((td.intent.confidence || 0) * 100);
            intentHtml = `
                <div class="mt-2 flex items-center gap-1.5 text-[11px] text-slate-500">
                    <span>${escapeHtml(t('trace.intent_label'))}</span>
                    <span class="font-medium text-slate-700">${escapeHtml(intentType)}</span>
                    <span class="text-slate-400">|</span>
                    <span>${escapeHtml(t('trace.intent_conf_pct').replace('{p}', confPct))}</span>
                </div>`;
        }

        const scoreNum = td.evaluation ? Math.round((td.evaluation.overall_score||0)*100) : null;
        oEl.innerHTML = `
            <div class="space-y-2 text-left">
                ${conclusionCard}
                <div class="flex items-center gap-2">
                    <span class="text-[10px] text-slate-500">${escapeHtml(t('trace.risk_level'))}</span>
                    <span class="text-sm px-3 py-1 rounded-full font-bold ${riskBg} ${riskColor} ${riskBorder} border">${escapeHtml(riskLabel)}</span>
                    ${(isLowQuality || noResult) ? `<span class="text-[10px] px-2 py-0.5 rounded-full font-medium bg-amber-100 text-amber-700 border border-amber-200">${escapeHtml(t('trace.badge_llm_supplement'))}</span>` : ''}
                </div>
                <div class="flex items-baseline gap-2">
                    ${scoreNum !== null ? `<span class="text-2xl font-bold ${riskColor}">${scoreNum}</span><span class="text-xs text-slate-400">${escapeHtml(t('trace.score_suffix'))}</span>` : ''}
                    <span class="text-xs text-slate-600">${escapeHtml(oneLine)}</span>
                </div>
                ${intentHtml}
            </div>
        `;
    }

    // ==================== Module 2 | Recall summary of the references ====================
    const rEl = document.getElementById('summaryRecallContent');
    if (td.results !== null) {
        if (!td.results || td.results.length === 0) {
            rEl.innerHTML = `<div class="text-center text-slate-400 py-3 text-xs">${escapeHtml(t('trace.no_reference'))}</div>`;
        } else {
            // Relevance level: high / medium / low (based on the fused score, no numbers shown)
            const levelOf = (score) => {
                if (score >= 0.5) return { label: t('trace.relevance_high'), cls: 'bg-green-100 text-green-700' };
                if (score >= 0.3) return { label: t('trace.relevance_medium'), cls: 'bg-yellow-100 text-yellow-700' };
                return { label: t('trace.relevance_low'), cls: 'bg-slate-100 text-slate-600' };
            };
            rEl.innerHTML = `
                <div class="text-[10px] text-slate-500 mb-2">
                    ${escapeHtml(t('trace.entered_answer'))} ${escapeHtml(t('trace.reference_items').replace('{n}', td.results.length))}
                    ${td.funnel && td.funnel.counts ? escapeHtml(t('trace.reference_counts').replace('{c}', td.funnel.counts.merged).replace('{d}', td.funnel.counts.dropped)) : ''}
                </div>
                <div class="space-y-2">
                    ${td.results.map((r, i) => {
                        const lv = levelOf(r.score || 0);
                        const preview = (r.text || '').length > 80 ? (r.text).slice(0, 80) + '…' : (r.text || '');
                        const reasons = (r.hit_reasons && r.hit_reasons.length > 0)
                            ? r.hit_reasons.map(hr => hitReasonText(hr)).filter(Boolean).join('；')
                            : t('trace.hit_reason_default');
                        return `
                            <div class="border border-slate-200 rounded-lg p-2">
                                <div class="flex items-center justify-between mb-1">
                                    <span class="text-xs font-medium text-slate-700 truncate flex-1 mr-2">${escapeHtml(r.metadata?.file_name || t('trace.reference_doc_n').replace('{n}', i + 1))}</span>
                                    <span class="text-[10px] px-1.5 py-0.5 rounded font-medium ${lv.cls} shrink-0">${escapeHtml(lv.label)}</span>
                                </div>
                                <div class="text-[11px] text-slate-500 leading-relaxed">${escapeHtml(preview)}</div>
                                <div class="text-[10px] text-emerald-600 mt-1">${escapeHtml(t('trace.hit_reason_inline').replace('{v}', reasons))}</div>
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
        }
    }

    // ==================== Module 3 | Sentence-level trust tracing of the AI answer ====================
    const sEl = document.getElementById('summarySentenceContent');
    if (td.sentenceTracing && td.sentenceTracing.length > 0) {
        // Qualitative tags: ✅ supported / ⚠️ low confidence / ❌ unsupported
        const tagOf = (level) => {
            if (level === 'citation_verified') return { icon: '🔗', label: t('trace.term.citation_verified'), cls: 'bg-teal-50 text-teal-700 border-teal-200' };
            if (level === 'direct_quote' || level === 'summary') return { icon: '✅', label: t('trace.sentence_supported'), cls: 'bg-green-50 text-green-700 border-green-200' };
            if (level === 'low_confidence') return { icon: '⚠️', label: t('trace.sentence_low_confidence'), cls: 'bg-yellow-50 text-yellow-700 border-yellow-200' };
            // Unverified: the tracing component failed, which is not a hallucination, so it
            // gets a neutral grey tag instead of red to avoid a wrong verdict
            if (level === 'unverified') return { icon: '❔', label: t('trace.term.unverified'), cls: 'bg-slate-100 text-slate-600 border-slate-200' };
            return { icon: '❌', label: t('trace.sentence_hallucination'), cls: 'bg-red-50 text-red-700 border-red-200' };
        };
        // Qualitative risk summary (no floating-point values shown)
        const driftCount = (td.driftAnalysis?.drift_rate || 0) * (td.driftAnalysis?.total_sentences || 0);
        let riskSummary = t('trace.risk_summary_good');
        if (riskLevel === 'red') riskSummary = t('trace.risk_summary_red').replace('{n}', Math.round(driftCount));
        else if (riskLevel === 'yellow') riskSummary = t('trace.risk_summary_yellow');
        // Unverified sentences (tracing component failed) are called out separately so
        // they are not read as hallucinations
        const unverifiedSentences = (td.driftAnalysis && td.driftAnalysis.unverified_count) || 0;
        if (unverifiedSentences > 0) {
            riskSummary += ` ${t('trace.risk_summary_unverified').replace('{n}', unverifiedSentences)}`;
        }
        // Statements that disagree with the source on a value or a polarity
        const contradictionList = td.contradictions || [];
        if (contradictionList.length > 0) {
            riskSummary += ` ${t('trace.contradiction_summary').replace('{n}', contradictionList.length)}`;
        }

        sEl.innerHTML = `
            ${td.citationValidation ? `<div class="mb-2 p-2 rounded-lg border border-indigo-100 bg-indigo-50/60">${citationValidationHtml(td.citationValidation)}</div>` : ''}
            <div class="text-[11px] text-slate-600 mb-2 px-1">${escapeHtml(riskSummary)}</div>
            ${contradictionList.length > 0 ? `
                <div class="mb-2 p-2 rounded-lg border border-red-200 bg-red-50/70 space-y-1">
                    <div class="text-[11px] font-semibold text-red-700">${escapeHtml(t('trace.contradiction_title'))}</div>
                    ${contradictionList.map(item => `
                        <div class="text-[10px] text-red-700 leading-relaxed">
                            • ${escapeHtml(item.explanation || '')}
                            ${item.source_sentence ? `<span class="text-red-500/90">（${escapeHtml(t('trace.contradiction_source'))}${escapeHtml(item.source_sentence)}）</span>` : ''}
                        </div>
                    `).join('')}
                </div>` : ''}
            <div class="space-y-1.5">
                ${td.sentenceTracing.map((trace, i) => {
                    const tag = tagOf(trace.confidence_level);
                    const contraTag = trace.has_contradiction
                        ? `<span class="text-[10px] px-1.5 py-0.5 rounded border bg-red-50 text-red-700 border-red-200 shrink-0">${escapeHtml(t('trace.contradiction_badge'))}</span>`
                        : '';
                    return `
                        <div class="border border-slate-200 rounded-lg p-2 ${trace.is_drift || trace.has_contradiction ? 'bg-red-50/40' : ''}">
                            <div class="flex items-start gap-2">
                                <span class="text-[10px] text-slate-400 mt-0.5 shrink-0">${escapeHtml(t('trace.sentence_n').replace('{n}', i + 1))}</span>
                                <div class="flex-1 min-w-0">
                                    <div class="text-[11px] text-slate-700 leading-relaxed break-words">${escapeHtml(trace.sentence)}</div>
                                </div>
                                ${contraTag}
                                <span class="text-[10px] px-1.5 py-0.5 rounded border font-medium shrink-0 ${tag.cls}">${tag.icon} ${escapeHtml(tag.label)}</span>
                            </div>
                            ${(trace.sources && trace.sources.length > 0) ? `
                                <button onclick="showSentenceSource(${i})"
                                        class="mt-1 text-[10px] text-blue-500 hover:text-blue-700 hover:bg-blue-50 px-1.5 py-0.5 rounded transition-colors">
                                    ${escapeHtml(t('trace.view_supporting_source').replace('{n}', trace.sources.length))}
                                </button>` : ''}
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    } else if (td.driftAnalysis) {
        sEl.innerHTML = `<div class="text-center text-slate-400 py-3 text-xs">${escapeHtml(t('trace.sentence_empty'))}</div>`;
    }

    // ==================== Module 4 | Smart diagnostics & optimization suggestions ====================
    const aEl = document.getElementById('summaryActionsContent');
    const tips = [];
    // Prefer the suggestions from the diagnostics module, but filter out technical jargon
    if (td.diagnosis && td.diagnosis.summary && td.diagnosis.summary.suggestions) {
        td.diagnosis.summary.suggestions.forEach(s => {
            // Filter out suggestions containing technical parameters (not allowed in the business view)
            if (!/(top_k|embedding|阈值|bm25|向量模型|分块策略|相似度阈值)/i.test(s)) tips.push(s);
        });
    }
    // Generate business-language suggestions dynamically from the real data
    if (td.evaluation) {
        const sc = td.evaluation.overall_score || 0;
        if (sc > 0 && sc < 0.6) tips.push(t('trace.tip_quality_low'));
        else if (sc > 0 && sc < 0.8) tips.push(t('trace.tip_quality_mid'));
    }
    if (td.driftAnalysis && (td.driftAnalysis.drift_rate || 0) > 0.2) {
        tips.push(t('trace.tip_many_drift'));
    }
    if (td.results && td.results.length > 0) {
        const avgSim = td.results.reduce((s, r) => s + (r.score || 0), 0) / td.results.length;
        if (avgSim < 0.4) tips.push(t('trace.tip_weak_recall'));
        if (td.results.length < 3) tips.push(t('trace.tip_few_refs'));
    }
    // Candidates dropped by the funnel: spell out the concrete tuning direction for "should have been recalled but never reached the answer"
    if (td.funnel && td.funnel.counts && td.funnel.counts.dropped > 0) {
        const stages = {};
        (td.funnel.dropped || []).forEach(d => { stages[d.stage] = (stages[d.stage] || 0) + 1; });
        if (stages.threshold) tips.push(t('trace.tip_tune_threshold').replace('{n}', stages.threshold));
        if (stages.top_k) tips.push(t('trace.tip_tune_top_k').replace('{n}', stages.top_k));
        if (stages.compression) tips.push(t('trace.tip_tune_compression').replace('{n}', stages.compression));
    }
    if (td.skipped && td.skipped.potential_misses) {
        tips.push(t('trace.tip_no_full_scan'));
    }
    if (tips.length === 0) tips.push(t('trace.tip_all_good'));

    const bizHtml = td.businessDiagnosis && td.businessDiagnosis.overall_status ? `
        <div class="mb-3 p-2 rounded-lg border ${bizStatusClass(td.businessDiagnosis.overall_status)}">
            <div class="flex items-center gap-1.5 mb-1">
                <span class="text-[10px] font-semibold">${escapeHtml(t('biz.title'))}</span>
                <span class="text-[9px] px-1.5 py-0.5 rounded-full border ${bizStatusClass(td.businessDiagnosis.overall_status)}">${escapeHtml(bizStatusLabel(td.businessDiagnosis.overall_status))}</span>
                ${td.businessDiagnosis.confidence_level ? `<span class="text-[9px] text-slate-500">${escapeHtml(t('biz.confidence').replace('{v}', td.businessDiagnosis.confidence_level))}</span>` : ''}
            </div>
            <div class="text-[10px] text-slate-600">
                ${escapeHtml(t('biz.avg_similarity').replace('{v}', fmtScore(td.avgRetrievalScore || 0)))}
            </div>
        </div>` : '';
    if (bizHtml) tips.push(t('trace.tip_biz_detail'));

    aEl.innerHTML = `
        ${bizHtml}
        <ul class="space-y-1.5 text-left">
            ${tips.map(s => `
                <li class="flex items-start gap-1.5 text-xs text-slate-600">
                    <span class="text-blue-500 mt-0.5">•</span>
                    <span>${escapeHtml(s)}</span>
                </li>
            `).join('')}
        </ul>
    `;

    // ==================== Module 5 | Quality metrics (collapsed by default) ====================
    const mEl = document.getElementById('summaryMetricsContent');
    if (td.evaluation && td.evaluation.metrics) {
        // Keep only the plain-language metrics, all as percentages, with localized names and tooltips and no English machine codes
        const POPULAR = {
            // Legacy v1 keys stay mapped so old traces still render in the business view.
            'retrieval_recall': { nameKey: 'trace.retrieval_quality', descKey: 'trace.biz_metric.retrieval_quality.desc' },
            'retrieval_score_avg': { nameKey: 'trace.retrieval_quality', descKey: 'trace.biz_metric.retrieval_quality.desc' },
            'answer_relevance': { nameKey: 'trace.answer_relevance', descKey: 'trace.biz_metric.answer_relevance.desc' },
            'answer_faithfulness': { nameKey: 'trace.faithfulness', descKey: 'trace.biz_metric.faithfulness.desc' },
            'hallucination_rate': { nameKey: 'trace.hallucination_risk', descKey: 'trace.biz_metric.hallucination_risk.desc' },
            'context_usage_ratio': { nameKey: 'trace.metric_context_usage', descKey: 'trace.biz_metric.context_usage.desc' },
            'citation_coverage': { nameKey: 'trace.citation_coverage', descKey: 'trace.biz_metric.citation_coverage.desc' }
        };
        let mHtml = '<div class="space-y-2">';
        for (const [key, metric] of Object.entries(td.evaluation.metrics)) {
            const info = POPULAR[key];
            if (!info) continue; // The business view only shows plain-language metrics
            // Show everything as percentages
            const pctText = (typeof metric.value === 'number') ? fmtRatio(metric.value) : '-';
            const passDot = (metric.is_pass === true) ? '<span class="w-1.5 h-1.5 rounded-full bg-green-500 inline-block"></span>'
                : (metric.is_pass === false ? '<span class="w-1.5 h-1.5 rounded-full bg-red-500 inline-block"></span>' : '');
            const name = t(info.nameKey);
            const desc = t(info.descKey);
            mHtml += `
                <div class="flex items-center justify-between text-xs gap-2">
                    <span class="text-slate-600 flex items-center gap-1">
                        ${escapeHtml(name)}
                        <svg class="w-3 h-3 text-slate-400 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24" title="${escapeHtml(desc)}"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    </span>
                    <span class="flex items-center gap-1.5">
                        <span class="font-medium text-slate-700">${pctText}</span>
                        ${passDot}
                    </span>
                </div>
            `;
        }
        mHtml += '</div>';
        mEl.innerHTML = mHtml;
    }
}

// Module 3 sentence tracing: the click modal shows the supporting source text (read from traceData to avoid JSON escaping issues)
// Highlight the matched chunk: extract the sentence keywords and highlight them in the source text
function extractHighlightTerms(sentence) {
    const terms = new Set();
    if (!sentence) return [];
    // English / numeric tokens (length >= 2)
    const enMatches = sentence.match(/[A-Za-z0-9]{2,}/g) || [];
    enMatches.forEach(t => terms.add(t));
    // CJK 2-grams (improves the hit rate for Chinese recall)
    const cjkRuns = sentence.match(/[\u4e00-\u9fa5]+/g) || [];
    cjkRuns.forEach(run => {
        for (let i = 0; i < run.length - 1; i++) {
            terms.add(run.substr(i, 2));
        }
        // A whole run of CJK characters also counts as one term
        if (run.length >= 2 && run.length <= 8) terms.add(run);
    });
    return Array.from(terms).filter(t => t.length >= 2);
}

function highlightMatches(text, terms) {
    if (!text) return '';
    if (!terms || terms.length === 0) return escapeHtml(text);
    const sorted = [...new Set(terms)].filter(t => t && t.length >= 2).sort((a, b) => b.length - a.length);
    if (sorted.length === 0) return escapeHtml(text);
    const pattern = sorted.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const re = new RegExp(pattern, 'gi');
    let result = '';
    let lastIdx = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
        result += escapeHtml(text.slice(lastIdx, m.index));
        result += `<mark class="bg-yellow-200 rounded px-0.5 text-slate-800">${escapeHtml(m[0])}</mark>`;
        lastIdx = m.index + m[0].length;
        if (m[0].length === 0) { re.lastIndex++; }
    }
    result += escapeHtml(text.slice(lastIdx));
    return result;
}

// Look up the full text by chunk_id among the retrieved results (improves the highlight coverage)
// Note: the text sent during streaming may be truncated, so a result carrying the text_truncated flag is not treated as the full text.
function findFullChunkText(chunkId) {
    const td = activeTraceData();
    const results = td && td.results;
    if (!results || !chunkId) return null;
    for (const r of results) {
        const rid = r.id || r.chunk_id || (r.metadata && r.metadata.chunk_id);
        if (rid && String(rid) === String(chunkId)) {
            if (r.text_truncated) return null;
            return { text: r.text || '', fileName: r.metadata && r.metadata.file_name };
        }
    }
    return null;
}

function centerModal(panelId) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    panel.style.left = '';
    panel.style.top = '';
    panel.style.transform = '';
    const rect = panel.getBoundingClientRect();
    const cx = window.innerWidth / 2 - rect.width / 2;
    const cy = window.innerHeight / 2 - rect.height / 2;
    panel.style.left = Math.max(0, cx) + 'px';
    panel.style.top = Math.max(0, cy) + 'px';
    panel.style.position = 'fixed';
}

function enableModalResize(panelId, handleId) {
    const panel = document.getElementById(panelId);
    const handle = document.getElementById(handleId);
    if (!panel || !handle) return;

    let resizing = false;
    let startX = 0, startW = 0, startLeft = 0;

    handle.addEventListener('mousedown', (e) => {
        resizing = true;
        startX = e.clientX;
        const rect = panel.getBoundingClientRect();
        startW = rect.width;
        startLeft = rect.left;
        e.preventDefault();
        e.stopPropagation();
    });

    document.addEventListener('mousemove', (e) => {
        if (!resizing) return;
        const dx = e.clientX - startX;
        const minW = 320;
        const maxW = Math.min(window.innerWidth - 40, 1200);
        let newW = Math.max(minW, Math.min(maxW, startW + dx));
        panel.style.width = newW + 'px';
        panel.style.left = (startLeft - (newW - startW)) + 'px';
        panel.style.transform = 'none';
        e.preventDefault();
    });

    document.addEventListener('mouseup', () => {
        resizing = false;
    });
}

function enableModalDrag(modalId, headerId, panelId) {
    const header = document.getElementById(headerId);
    const panel = document.getElementById(panelId);
    if (!header || !panel) return;

    let dragging = false;
    let offsetX = 0, offsetY = 0;
    const closeBtn = header.querySelector('button');

    header.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        if (closeBtn && closeBtn.contains(e.target)) return;
        dragging = true;
        const rect = panel.getBoundingClientRect();
        offsetX = e.clientX - rect.left;
        offsetY = e.clientY - rect.top;
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        let nx = e.clientX - offsetX;
        let ny = e.clientY - offsetY;
        const maxX = window.innerWidth - panel.offsetWidth;
        const maxY = window.innerHeight - panel.offsetHeight;
        nx = Math.max(-panel.offsetWidth + 80, Math.min(nx, window.innerWidth - 80));
        ny = Math.max(-40, Math.min(ny, window.innerHeight - 40));
        panel.style.left = nx + 'px';
        panel.style.top = ny + 'px';
        panel.style.transform = 'none';
        e.preventDefault();
    });

    document.addEventListener('mouseup', () => {
        dragging = false;
    });
}

async function showSentenceSource(idx, ev) {
    const el = ev && ev.currentTarget ? ev.currentTarget : null;
    const td = traceDataForElement(el);
    const trace = td.sentenceTracing && td.sentenceTracing[idx];
    if (!trace) {
        showToast('info', t('trace.sentence_source_missing'));
        return;
    }
    const sources = trace.sources || [];
    const sentence = trace.sentence || '';
    const level = trace.confidence_level || 'unknown';
    // Colours are handled centrally by themeLevelColor, which picks a value per theme
    // Confidence level code -> localized label from the shared glossary.
    const levelEntry = TRACE_TERM_MAP[level] || null;
    const color = themeLevelColor(level);
    const label = termName(levelEntry, t('trace.term.unknown'));

    // Extract the sentence keywords used to highlight the source text
    const terms = extractHighlightTerms(sentence);

    // Resolve the full text of every source first: a local hit is used as-is, otherwise the chunk
    // details are fetched on demand.
    // That way even a chunk outside the top_k display list yields the complete source instead of a
    // 100-character preview.
    const resolved = await Promise.all(sources.map(async (s) => {
        const local = findFullChunkText(s.chunk_id);
        if (local && local.text) return { full: local, preview: s.text_preview || s.text || '' };
        const fetched = await fetchChunkText(s.chunk_id);
        return { full: fetched, preview: s.text_preview || s.text || '' };
    }));

    let html = '';
    // Top: the traced sentence + its confidence label
    html += `
        <div class="bg-slate-50 rounded-lg p-3 mb-3 border-l-4" style="border-left-color:${color}">
            <div class="flex items-center gap-2 mb-1">
                <span class="text-[10px] text-slate-400">${escapeHtml(t('trace.traced_sentence'))}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded text-white" style="background:${color}">${escapeHtml(label)}</span>
            </div>
            <div class="text-sm text-slate-700 leading-relaxed">${escapeHtml(sentence)}</div>
        </div>
    `;

    if (sources.length === 0) {
        html += `<div class="text-center text-slate-400 py-6 text-sm">${escapeHtml(t('trace.no_supporting_source'))}</div>`;
    } else {
        html += `<div class="text-[10px] text-slate-400 mb-2">${t('trace.highlight_note').replace('{n}', sources.length)}</div>`;
        html += sources.map((s, i) => {
            // Prefer the full chunk text, fall back to the preview
            const full = resolved[i].full;
            const rawText = (full && full.text) ? full.text : resolved[i].preview;
            const fileName = (full && full.fileName) || (s.metadata && s.metadata.file_name) || t('trace.unknown_doc');
            const score = (typeof s.score === 'number') ? s.score : 0;
            const scorePct = Math.round(score * 100);
            const highlighted = highlightMatches(rawText, terms);
            return `
                <div class="border border-slate-200 rounded-lg p-2 mb-2">
                    <div class="flex items-center justify-between mb-1.5">
                        <div class="flex items-center gap-2">
                            <span class="text-[10px] text-slate-400">${escapeHtml(t('trace.ref_n').replace('{n}', i + 1))}</span>
                            <span class="text-[10px] text-slate-500 truncate max-w-[180px]" title="${escapeHtml(fileName)}">📄 ${escapeHtml(fileName)}</span>
                        </div>
                        <div class="flex items-center gap-1">
                            <span class="text-[10px] text-slate-400">${escapeHtml(t('trace.match_degree'))}</span>
                            <span class="text-[10px] font-bold ${scorePct >= 70 ? 'text-green-600' : scorePct >= 40 ? 'text-yellow-600' : 'text-red-500'}">${scorePct}%</span>
                        </div>
                    </div>
                    <div class="text-xs text-slate-600 whitespace-pre-wrap leading-relaxed bg-yellow-50/40 rounded p-1.5">${highlighted || `<span class="text-slate-400">${escapeHtml(t('trace.no_raw_text'))}</span>`}</div>
                </div>
            `;
        }).join('');
    }

    // Simple modal (same idea as the toast, using a fixed layer)
    let modal = document.getElementById('sentenceSourceModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'sentenceSourceModal';
        modal.className = 'fixed inset-0 z-50 bg-black/40';
        modal.onclick = (e) => { if (e.target === modal) modal.classList.add('hidden'); };
        modal.innerHTML = `
            <div id="sentenceSourceModalPanel" class="relative bg-white rounded-lg shadow-xl w-[480px] max-w-[90vw] max-h-[75vh] flex flex-col">
                <div id="sentenceSourceModalHeader" class="px-4 py-2 border-b border-slate-200 flex items-center justify-between cursor-move select-none">
                    <span class="text-sm font-semibold text-slate-700">${escapeHtml(t('trace.chunk_source_title'))}</span>
                    <button onclick="document.getElementById('sentenceSourceModal').classList.add('hidden')" class="text-slate-400 hover:text-slate-600 cursor-pointer">✕</button>
                </div>
                <div id="sentenceSourceModalBody" class="p-3 overflow-y-auto flex-1"></div>
                <div id="sentenceSourceModalResizeHandle" class="absolute top-0 right-0 h-full w-[6px] cursor-ew-resize bg-transparent hover:bg-blue-300/60 rounded-r-lg group"></div>
            </div>
        `;
        document.body.appendChild(modal);
        enableModalDrag('sentenceSourceModal', 'sentenceSourceModalHeader', 'sentenceSourceModalPanel');
        enableModalResize('sentenceSourceModalPanel', 'sentenceSourceModalResizeHandle');
    }
    document.getElementById('sentenceSourceModalBody').innerHTML = html;
    modal.classList.remove('hidden');
    centerModal('sentenceSourceModalPanel');
}

// Reset the trace panel when a new conversation starts so no stale data lingers
function resetTracePanel() {
    state.traceData = {
        results: null, mode: null, count: 0, debugInfo: null,
        evaluation: null, intent: null, sentenceTracing: null,
        driftAnalysis: null, diagnosis: null,
        userQuery: null, aiAnswer: null,
        retrievalQuality: 'low', avgRetrievalScore: 0,
        funnel: null, citationValidation: null, businessDiagnosis: null, skipped: null
    };
    state.panelAutoOpened = false;
    resetTracePanelDom();
}

/** Clear the panel DOM only, leaving state untouched (reused when switching history snapshots) */
function resetTracePanelDom() {
    // Reset the summary view placeholders (the new ids of the 5 modules)
    document.getElementById('summaryOverviewContent').innerHTML = `<span class="text-slate-400 text-xs">${escapeHtml(t('trace.no_data_yet'))}</span>`;
    document.getElementById('summaryRecallContent').innerHTML = `<span class="text-slate-400 text-xs">${escapeHtml(t('trace.no_data'))}</span>`;
    const funnelEl = document.getElementById('summaryFunnelContent');
    if (funnelEl) funnelEl.innerHTML = `<span class="text-slate-400 text-xs">${escapeHtml(t('trace.no_data'))}</span>`;
    document.getElementById('summarySentenceContent').innerHTML = `<span class="text-slate-400 text-xs">${escapeHtml(t('trace.no_data'))}</span>`;
    document.getElementById('summaryActionsContent').innerHTML = `<span class="text-slate-400 text-xs">${escapeHtml(t('trace.no_suggestions'))}</span>`;
    document.getElementById('summaryMetricsContent').innerHTML = `<span class="text-slate-400 text-xs">${escapeHtml(t('trace.expand_metrics'))}</span>`;
    // Hide the technical detail cards
    ['debugPanel', 'evaluationCard', 'intentCard', 'sentenceTracingCard', 'recallDiagnosisCard',
     'funnelPanel', 'citationValidationCard', 'businessDiagnosisCard'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
    });
    // Reset the retrieval result placeholder
    const sourceList = document.getElementById('sourceList');
    if (sourceList) {
        sourceList.innerHTML = `<div class="text-center text-slate-400 py-8"><p class="text-sm">${escapeHtml(t('trace.no_trace'))}</p><p class="text-xs mt-1">${escapeHtml(t('trace.no_trace_hint'))}</p></div>`;
    }
    const badge = document.getElementById('traceBadge');
    if (badge) badge.textContent = '';
}
