// Runs in <head> before first paint so there is no light/dark flash. Dark is the default.
try { if (localStorage.getItem('theme') !== 'light') document.documentElement.classList.add('dark'); }
catch (_) { document.documentElement.classList.add('dark'); }
