function blinkApp() {
  return {
    authenticated: document.getElementById('app').dataset.authenticated === 'true',

    // Theme
    theme: localStorage.getItem('blink-theme') || 'dark',

    // Blink system state
    blinkConnected: false,
    systemArmed:    null,
    statusLoading:  false,
    toggleLoading:  false,
    lastUpdated:    null,

    // Blink auth state
    needsBlinkLogin: false,
    awaiting2FA:     false,

    // Videos
    videos:        [],
    videosFetched:  false,
    videosLoading:  false,
    activeVideo:    null,
    remoteFetching: false,
    settings:       { scheduled_download: { enabled: false, time: '04:00', timezone: 'UTC' } },
    settingsOpen:   false,
    settingsSaving: false,

    // Forms
    creds:        { email: '', password: '' },
    showPassword:  false,
    credsLoading:  false,
    credsError:    '',
    pinCode:       '',
    pinLoading:    false,
    pinError:      '',

    // Logo menu / reset modal
    showLogoMenu:  false,
    showResetModal: false,
    resetLoading:  false,

    // Language switcher
    currentLang:  document.documentElement.lang || 'en',
    showLangMenu: false,

    // Pull-to-refresh
    ptrStartY:    0,
    ptrH:         0,
    ptrRot:       0,
    ptrRefreshing: false,

    // Toast
    toast:       { show: false, message: '', type: 'info' },
    _toastTimer: null,

    // ── Computed ──────────────────────────────────────────────────
    get currentView() {
      if (!this.authenticated)  return 'google-login';
      if (this.statusLoading)   return 'loading';
      if (this.awaiting2FA)     return 'blink-2fa';
      if (!this.blinkConnected) return 'blink-setup';
      return 'dashboard';
    },

    get statusLabel() {
      const i = window.APP_I18N;
      if (!this.blinkConnected)       return i.status_disconnected;
      if (this.systemArmed === true)  return i.status_armed;
      if (this.systemArmed === false) return i.status_disarmed;
      return i.status_unknown;
    },

    get videosByDate() {
      const locale       = window.APP_I18N.date_locale;
      const todayStr     = new Date().toLocaleDateString(locale);
      const yesterdayStr = new Date(Date.now() - 864e5).toLocaleDateString(locale);
      const groups       = new Map();
      for (const v of this.videos) {
        if (!groups.has(v.date_key)) {
          let label = v.date_key;
          if (v.date_key === todayStr)          label = window.APP_I18N.today;
          else if (v.date_key === yesterdayStr) label = window.APP_I18N.yesterday;
          else if (v.created_at) {
            const d = new Date(v.created_at);
            label = d.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long' });
            label = label.charAt(0).toUpperCase() + label.slice(1);
          }
          groups.set(v.date_key, { label, videos: [] });
        }
        groups.get(v.date_key).videos.push(v);
      }
      return Array.from(groups.values());
    },

    // ── Lifecycle ─────────────────────────────────────────────────
    async init() {
      this.applyTheme(this.theme);
      if (!this.authenticated) return;
      await this.fetchStatus();
      this.fetchVideos();
    },

    // ── Theme ─────────────────────────────────────────────────────
    toggleTheme() {
      this.theme = this.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('blink-theme', this.theme);
      this.applyTheme(this.theme);
    },

    applyTheme(t) {
      const isDark = t === 'dark';
      document.documentElement.classList.toggle('dark', isDark);
      const meta = document.getElementById('theme-color-meta');
      if (meta) meta.content = isDark ? '#09090E' : '#F1F5F9';
    },

    // ── API: Status ───────────────────────────────────────────────
    async fetchStatus() {
      this.statusLoading = true;
      try {
        const res  = await fetch('/api/status');
        const data = await res.json();
        this.blinkConnected  = data.blink_connected ?? false;
        this.systemArmed     = data.armed           ?? null;
        this.awaiting2FA     = data.awaiting_2fa    ?? false;
        this.needsBlinkLogin = !data.blink_connected && !data.awaiting_2fa;
        this.lastUpdated     = new Date().toLocaleTimeString(window.APP_I18N.date_locale, { hour: '2-digit', minute: '2-digit' });
        if (this.awaiting2FA) {
          this.$nextTick(() => this.$refs.pinInputScreen?.focus());
        }
      } catch (err) {
        console.error('[status]', err);
        this.showToast(window.APP_I18N.error_connection, 'error');
      } finally {
        this.statusLoading = false;
      }
    },

    // ── API: Arm / Disarm ─────────────────────────────────────────
    async toggleArm() {
      if (this.toggleLoading || !this.blinkConnected) return;
      const endpoint = this.systemArmed ? '/api/blink/disarm' : '/api/blink/arm';
      this.toggleLoading = true;
      try {
        const res  = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error ?? window.APP_I18N.error_connection);
        this.systemArmed = data.armed ?? !this.systemArmed;
        this.lastUpdated = new Date().toLocaleTimeString(window.APP_I18N.date_locale, { hour: '2-digit', minute: '2-digit' });
        this.showToast(this.systemArmed ? window.APP_I18N.system_armed : window.APP_I18N.system_disarmed, 'success');
      } catch (err) {
        this.showToast(err.message ?? 'Errore', 'error');
      } finally {
        this.toggleLoading = false;
      }
    },

    // ── API: Videos ───────────────────────────────────────────────
    async fetchVideos() {
      if (this.videosLoading) return;
      const inFlight = this.videos.filter(v => v.state === 'downloading');
      this.videosLoading = true;
      try {
        const res  = await fetch('/api/local-videos');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error ?? 'Errore');
        const seen = new Set();
        this.videos = (data.videos ?? [])
          .map(v => ({ ...this._mapVideo(v), state: 'local' }))
          .filter(v => { if (seen.has(v.id)) return false; seen.add(v.id); return true; });
        const localIds = new Set(this.videos.map(v => v.id));
        for (const v of inFlight) {
          if (!localIds.has(v.id)) this.videos.push(v);
        }
        this.videosFetched = true;
      } catch (err) {
        console.error('[videos]', err);
        this.showToast(window.APP_I18N.videos_error, 'error');
        this.videosFetched = true;
      } finally {
        this.videosLoading = false;
      }
      if (this.blinkConnected) {
        this.fetchRemoteClips();
      }
    },

    _mapVideo(v) {
      const d      = v.created_at ? new Date(v.created_at) : null;
      const locale = window.APP_I18N.date_locale;
      return {
        ...v,
        created_at_fmt: d ? d.toLocaleString(locale, { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' }) : '—',
        time_fmt:       d ? d.toLocaleTimeString(locale, { hour:'2-digit', minute:'2-digit' }) : '—',
        date_key:       d ? d.toLocaleDateString(locale) : '',
      };
    },

    playVideo(video) { this.activeVideo = video; },

    async fetchRemoteClips() {
      if (this.remoteFetching) return;
      this.remoteFetching = true;
      try {
        const res  = await fetch('/api/blink/local/remote');
        const data = await res.json();
        if (!res.ok) return;
        const localIds = new Set(this.videos.map(v => v.id));
        const remotes = (data.clips ?? [])
          .filter(c => !localIds.has(c.id))
          .map(c => ({ ...this._mapVideo(c), state: 'remote' }));
        this.videos = [...this.videos, ...remotes]
          .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      } catch (err) {
        console.error('[remote]', err);
      } finally {
        this.remoteFetching = false;
      }
    },

    async boostClip(video) {
      if (video.state !== 'remote') { this.playVideo(video); return; }
      video.state = 'downloading';
      try {
        const res = await fetch('/api/blink/local/clip/boost', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ module: video.module, clip_id: video.id }),
        });
        if (!res.ok) { video.state = 'remote'; this.showToast(window.APP_I18N.videos_error, 'error'); return; }
        this._pollClip(video);
      } catch (err) {
        video.state = 'remote';
        this.showToast(window.APP_I18N.videos_error, 'error');
      }
    },

    _pollClip(video, attempts = 0) {
      if (attempts > 60) { video.state = 'remote'; return; }
      setTimeout(async () => {
        try {
          const res  = await fetch('/api/local-videos');
          const data = await res.json();
          const found = (data.videos ?? []).find(v => v.id === video.id);
          if (found) {
            Object.assign(video, this._mapVideo(found), { state: 'local' });
          } else {
            this._pollClip(video, attempts + 1);
          }
        } catch (err) {
          this._pollClip(video, attempts + 1);
        }
      }, 3000);
    },

    async openSettings() {
      this.settingsOpen = true;
      this.showLogoMenu = false;
      try {
        const res = await fetch('/api/settings');
        if (res.ok) this.settings = await res.json();
      } catch (err) { console.error('[settings]', err); }
    },

    async saveSettings() {
      this.settingsSaving = true;
      try {
        const res = await fetch('/api/settings', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.settings),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error ?? 'Errore');
        this.settingsOpen = false;
        this.showToast('OK', 'success');
      } catch (err) {
        this.showToast(err.message ?? 'Errore', 'error');
      } finally {
        this.settingsSaving = false;
      }
    },

    // ── API: Blink Login ──────────────────────────────────────────
    async submitCredentials() {
      this.credsLoading = true;
      this.credsError   = '';
      try {
        const res  = await fetch('/api/credentials', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.creds),
        });
        const data = await res.json();
        if (data.awaiting_2fa) {
          this.awaiting2FA = true;
          this.$nextTick(() => this.$refs.pinInputScreen?.focus());
        } else if (res.ok) {
          this.needsBlinkLogin = false;
          this.blinkConnected  = true;
          this.showToast(window.APP_I18N.blink_login_ok, 'success');
          await this.fetchStatus();
        } else {
          this.credsError = data.error ?? window.APP_I18N.connection_retry;
        }
      } catch (err) {
        this.credsError = window.APP_I18N.connection_retry;
      } finally {
        this.credsLoading = false;
      }
    },

    // ── API: 2FA ──────────────────────────────────────────────────
    async submit2FA() {
      if (!this.pinCode) return;
      this.pinLoading = true;
      this.pinError   = '';
      try {
        const res  = await fetch('/api/credentials/2fa', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: String(this.pinCode) }),
        });
        const data = await res.json();
        if (res.ok) {
          this.pinCode         = '';
          this.awaiting2FA     = false;
          this.needsBlinkLogin = false;
          this.blinkConnected  = true;
          this.showToast(window.APP_I18N.verify_ok, 'success');
          await this.fetchStatus();
        } else if (data.session_expired) {
          this.pinCode     = '';
          this.awaiting2FA = false;
          this.creds       = { email: '', password: '' };
          this.credsError  = window.APP_I18N.session_expired;
        } else {
          this.pinError = data.error ?? window.APP_I18N.connection_retry;
          this.pinCode  = '';
        }
      } catch (err) {
        this.pinError = window.APP_I18N.connection_retry;
      } finally {
        this.pinLoading = false;
      }
    },

    async changeLanguage(lang) {
      this.showLangMenu = false;
      if (lang === this.currentLang) return;
      const app = document.getElementById('app');
      app.style.transition = 'opacity 0.15s ease';
      app.style.opacity = '0';
      try {
        await fetch(`/api/language/${lang}`, { method: 'POST' });
      } catch (err) {
        app.style.opacity = '';
        console.error('[lang]', err);
        return;
      }
      setTimeout(() => window.location.reload(), 150);
    },

    async resetServer() {
      this.resetLoading = true;
      try {
        const res = await fetch('/api/admin/reset', { method: 'POST' });
        if (!res.ok) {
          const d = await res.json();
          throw new Error(d.errors?.join(', ') ?? 'Reset fallito');
        }
        window.location.href = '/logout';
      } catch (err) {
        this.showToast(err.message ?? 'Errore reset', 'error');
        this.resetLoading = false;
        this.showResetModal = false;
      }
    },

    logout() { window.location.href = '/logout'; },

    // ── Toast ─────────────────────────────────────────────────────
    showToast(message, type = 'info') {
      if (this._toastTimer) clearTimeout(this._toastTimer);
      this.toast = { show: true, message, type };
      this._toastTimer = setTimeout(() => { this.toast.show = false; }, 3500);
    },

    // ── Pull-to-refresh ───────────────────────────────────────────
    ptr_touchStart(e) {
      const el = document.getElementById('scroll-root');
      if (el.scrollTop !== 0) return;
      this.ptrStartY = e.touches[0].clientY;
    },
    ptr_touchMove(e) {
      if (this.ptrRefreshing || !this.ptrStartY) return;
      const el = document.getElementById('scroll-root');
      if (el.scrollTop !== 0) { this.ptrStartY = 0; return; }
      const delta = Math.max(0, e.touches[0].clientY - this.ptrStartY);
      this.ptrH   = Math.min(delta * 0.45, 68);
      this.ptrRot = (this.ptrH / 68) * 360;
    },
    async ptr_touchEnd() {
      if (this.ptrH > 52 && !this.ptrRefreshing) {
        this.ptrRefreshing = true;
        await this.blinkRefresh();
        await this.fetchVideos();
      }
      this.ptrH = 0; this.ptrStartY = 0; this.ptrRot = 0; this.ptrRefreshing = false;
    },

    async blinkRefresh() {
      try {
        const res  = await fetch('/api/blink/refresh', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.armed !== undefined) {
          this.systemArmed = data.armed;
          this.lastUpdated = new Date().toLocaleTimeString(window.APP_I18N.date_locale, { hour: '2-digit', minute: '2-digit' });
        }
      } catch (err) {
        console.error('[blink-refresh]', err);
      }
      await this.fetchStatus();
    },
  };
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(err => console.warn('[SW] registration failed:', err));
  });
}
