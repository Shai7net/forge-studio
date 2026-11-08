// The DeforumationQT bar above Deforum's preview, and the live control row under it. The buttons are built once at startup, so after a page
// reload they can show an old state: read the saved state and, when the page disagrees, click the hidden sync button.
// Gradio may not handle clicks yet right after the UI loads, so check again until the page matches.
onUiLoaded(function() {
    let attempts = 0;

    async function savedState() {
        try {
            const response = await fetch('file=extensions/sd-forge-deforum/deforumation_switch.json?t=' + Date.now());
            return response.ok ? Boolean((await response.json()).enabled) : true;
        } catch (e) {
            return true; // no saved choice yet: the extension treats that as ON
        }
    }

    async function sync() {
        const app = gradioApp();
        const on = app.querySelector('#deforumation_switch_on');
        const button = app.querySelector('#deforumation_switch_sync');
        if (!on || !button) return;
        const showsOn = !on.classList.contains('hidden');
        // the first click is always made: it also fills the live control row under the bar, which is built once
        // at startup too and would otherwise show the state Forge started with
        if (attempts === 0 || (showsOn !== await savedState() && attempts < 20)) {
            attempts++;
            button.click();
            setTimeout(sync, 1000);
        }
    }

    setTimeout(sync, 500);
});

// The line under the live control row. A render writes live_control_status.json on every frame, so this says what
// the picture is really doing and who decided it: with DeforumationQT ON, its own selector wins over the row.
onUiLoaded(function() {
    function hebrew() {
        return document.documentElement.classList.contains('hebrew-ui-active');
    }

    function describe(status) {
        if (!status || !status.running || !status.at || (Date.now() / 1000 - status.at) > 60) return '';
        const he = hebrew();
        const percent = Math.round((status.video || 0) * 100);
        const what = status.video_only ? (he ? 'וידאו' : 'the video init')
            : percent > 0 ? status.mode + ' + ' + (he ? 'וידאו' : 'video') + ' ' + percent + '%'
            : status.mode;
        const who = status.source === 'DeforumationQT' ? 'DeforumationQT'
            : status.source === 'bar' ? (he ? 'הסרגל' : 'this bar')
            : (he ? 'הפרויקט' : 'the project');
        const frame = (he ? 'פריים ' : 'frame ') + status.frame;
        const fading = status.fade_left
            ? ' · ' + (he ? 'דועך ל-' : 'fading to ') + Math.round(status.fade_to * 100) + '% (' + status.fade_left + ')'
            : status.fade_at ? ' · ' + (he ? 'דעיכה בפריים ' : 'fade at frame ') + status.fade_at : '';
        const next = status.next_at ? ' · ' + (he ? 'הבא: פריים ' : 'next: frame ')
            + status.next_at + ' → ' + status.next_what : '';
        return '<span dir="auto">▶ ' + frame + ': ' + what + ' · ' + who + fading + next + '</span>';
    }

    async function tick() {
        const element = gradioApp().querySelector('#live_control_status');
        if (element) {
            let status = null;
            try {
                const response = await fetch('file=extensions/sd-forge-deforum/live_control_status.json?t=' + Date.now());
                if (response.ok) status = await response.json();
            } catch (e) {
                status = null; // no render has ever run, or the file is being written right now
            }
            const html = describe(status);
            if (element.innerHTML !== html) element.innerHTML = html;
        }
        setTimeout(tick, 2000);
    }

    setTimeout(tick, 1500);
});
