/**
 * static/ab_test.html - entry
 *
 * Content: init() initializes the A/B test page, must be called after all other scripts.
 */

// Prevent duplicate invocations of init() (due to multiple page loads)
if (window.__abtest_initialized__) {
    console.warn('A/B test page init() already called, skipping duplicate invocation');
} else {
    window.__abtest_initialized__ = true;
    init();
}