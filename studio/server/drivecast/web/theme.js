try{var t=localStorage.getItem('dc_theme')||'auto';document.documentElement.dataset.theme=(t==='dark'||(t==='auto'&&matchMedia('(prefers-color-scheme: dark)').matches))?'dark':'light'}catch(e){}
