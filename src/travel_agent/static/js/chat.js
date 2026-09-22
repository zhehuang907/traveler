/* 小T 对话页：SSE 流式接收 + Markdown 渲染 + 多轮 thread 保持 */

if (window.marked) marked.setOptions({ breaks: true, gfm: true });

function chatApp() {
  return {
    input: '',
    messages: [],
    streaming: false,
    streamingText: '',
    plan: null,
    threadId: null,
    stickBottom: true,
    // 认证 + 我的行程
    me: null,
    authLoading: true,
    authSubmitting: false,
    authMode: 'login',
    authForm: { username: '', password: '' },
    authError: '',
    authErrorIsOk: false,
    plans: [],
    showPlans: false,
    planQuery: '',
    planTotal: 0,
    planOffset: 0,
    planLimit: 20,
    planLoadingMore: false,
    // 会话列表（历史会话抽屉）
    threads: [],
    showThreads: false,
    threadsLoading: false,
    suggestions: [
      { icon: '🐼', title: '规划一趟成都之旅', text: '成都4天3晚，预算5000，爱吃辣，帮我安排行程' },
      { icon: '🏔️', title: '第一次去高原', text: '第一次去川西高反严重吗？需要准备什么？' },
      { icon: '👨‍👩‍👧', title: '带老人孩子出行', text: '带老人和5岁小孩去北京玩3天，怎么安排不累？' },
      { icon: '🍜', title: '寻找地道美食', text: '广州3天美食路线，预算2000，有哪些必吃店？' },
    ],
    steps: [
      { name: 'intent', label: '理解需求', state: 'idle' },
      { name: 'search', label: '检索资料', state: 'idle' },
      { name: 'compose', label: '编排行程', state: 'idle' },
      { name: 'validate', label: '校验调整', state: 'idle' },
    ],

    async init() {
      // 恢复上次会话（thread_id 持久化在 localStorage，刷新/重开后继续同一线程）
      this.threadId = localStorage.getItem('travel_thread_id') || null;
      await this.bootstrapAuth();
    },

    async bootstrapAuth() {
      try {
        const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (res.ok) {
          this.me = await res.json();
          await this.loadPlans();
          await this.restoreHistory();
        } else {
          this.me = null;
        }
      } catch {
        this.me = null;
      }
      this.authLoading = false;
    },

    async restoreHistory() {
      if (!this.threadId) return;
      try {
        const res = await fetch('/api/chat/' + this.threadId + '/history', {
          credentials: 'same-origin',
        });
        if (!res.ok) {
          this.threadId = null;
          localStorage.removeItem('travel_thread_id');
          return;
        }
        const rows = await res.json();
        if (!Array.isArray(rows) || !rows.length) return;
        this.messages = rows.map((r, i) => ({
          id: Date.now() + i,
          role: r.role,
          content: r.content,
        }));
        this.$nextTick(() => this.scrollDown());
      } catch {
        /* 恢复失败静默，不影响新会话 */
      }
    },

    startNewChat() {
      if (this.streaming) return;
      this.messages = [];
      this.plan = null;
      this.streamingText = '';
      this.input = '';
      this.threadId = null;
      localStorage.removeItem('travel_thread_id');
      this.$nextTick(() => {
        const el = this.$refs.input;
        if (el) el.focus();
      });
    },

    toggleMode() {
      this.authMode = this.authMode === 'login' ? 'register' : 'login';
      this.authError = '';
      this.authErrorIsOk = false;
    },

    async submitAuth() {
      const path = this.authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
      this.authError = '';
      this.authErrorIsOk = false;
      this.authSubmitting = true;
      try {
        const res = await fetch(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.authForm),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          this.authError = (data.error && data.error.message) || '操作失败，请检查输入后重试';
          return;
        }
        if (this.authMode === 'register') {
          // 注册成功后切回登录界面，提示用新账号登录（register 不签发会话）
          this.authMode = 'login';
          this.authForm.password = '';
          this.authError = '注册成功，请登录';
          this.authErrorIsOk = true;
          return;
        }
        this.me = data;
        this.authForm.password = '';
        await this.loadPlans();
      } catch {
        this.authError = '网络错误，请稍后重试';
      } finally {
        this.authSubmitting = false;
      }
    },

    async logout() {
      try {
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
      } catch { /* 忽略 */ }
      this.me = null;
      this.plans = [];
      this.showPlans = false;
      this.threads = [];
      this.showThreads = false;
      this.messages = [];
      this.plan = null;
      this.threadId = null;
      localStorage.removeItem('travel_thread_id');
    },

    /* ---------- 修改密码 ---------- */

    showChangePw: false,
    changePwForm: { old_password: '', new_password: '' },
    changePwError: null,
    changePwOk: null,
    changePwSubmitting: false,

    async changePassword() {
      this.showChangePw = true;
      this.changePwForm = { old_password: '', new_password: '' };
      this.changePwError = null;
      this.changePwOk = null;
    },

    async submitChangePassword() {
      if (this.changePwSubmitting) return;
      this.changePwSubmitting = true;
      this.changePwError = null;
      this.changePwOk = null;
      try {
        const res = await fetch('/api/auth/change-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(this.changePwForm),
        });
        if (res.ok) {
          this.changePwOk = '密码已更新，请重新登录';
          this.showChangePw = false;
          await this.logout();
          return;
        }
        const data = await res.json().catch(() => null);
        this.changePwError = (data && data.error && data.error.message) || '修改失败';
      } catch {
        this.changePwError = '网络错误，请稍后重试';
      } finally {
        this.changePwSubmitting = false;
      }
    },

    async loadPlans() {
      try {
        const params = new URLSearchParams({ limit: String(this.planLimit) });
        if (this.planQuery) params.set('q', this.planQuery);
        const res = await fetch('/api/plan?' + params.toString(), { credentials: 'same-origin' });
        if (res.ok) {
          this.plans = await res.json();
          this.planTotal = Number(res.headers.get('X-Total-Count') || this.plans.length);
          this.planOffset = 0;
        }
      } catch { /* 忽略加载失败 */ }
    },

    async searchPlans() {
      this.planOffset = 0;
      await this.loadPlans();
    },

    async loadMorePlans() {
      if (this.planLoadingMore) return;
      if (this.plans.length >= this.planTotal) return;
      this.planLoadingMore = true;
      try {
        const params = new URLSearchParams({
          limit: String(this.planLimit),
          offset: String(this.plans.length),
        });
        if (this.planQuery) params.set('q', this.planQuery);
        const res = await fetch('/api/plan?' + params.toString(), { credentials: 'same-origin' });
        if (res.ok) {
          const more = await res.json();
          this.plans = this.plans.concat(more);
          this.planTotal = Number(res.headers.get('X-Total-Count') || this.plans.length);
        }
      } catch { /* 忽略加载失败 */ }
      this.planLoadingMore = false;
    },

    async openPlans() {
      this.showPlans = true;
      await this.loadPlans();
    },

    startNewPlan() {
      this.showPlans = false;
      this.$nextTick(() => {
        const el = this.$refs.input;
        if (el) {
          this.input = '帮我规划一段新旅程…';
          el.focus();
        }
      });
    },

    async deletePlan(id) {
      if (!confirm('确定删除该行程？删除后不可恢复。')) return;
      const res = await fetch('/api/plan/' + id, { method: 'DELETE', credentials: 'same-origin' });
      if (res.ok || res.status === 404) await this.loadPlans();
    },

    async clonePlan(id) {
      const res = await fetch('/api/plan/' + id + '/clone', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (res.ok) await this.loadPlans();
    },

    /* ---------- 历史会话列表 / 删除 ---------- */

    async openThreads() {
      this.showThreads = true;
      await this.loadThreads();
    },

    async loadThreads() {
      if (this.threadsLoading) return;
      this.threadsLoading = true;
      try {
        const res = await fetch('/api/chat/threads', { credentials: 'same-origin' });
        if (res.ok) {
          const list = await res.json();
          this.threads = (list || []).map((t) => ({
            id: t.thread_id,
            preview: t.preview || '（空会话）',
            last_at: (t.last_message_at || '').replace('T', ' ').slice(0, 16),
          }));
        }
      } catch { /* 忽略加载失败 */ }
      this.threadsLoading = false;
    },

    switchThread(tid) {
      if (this.streaming) return;
      this.threadId = tid;
      localStorage.setItem('travel_thread_id', tid);
      this.showThreads = false;
      this.messages = [];
      this.plan = null;
      this.streamingText = '';
      this.restoreHistory();
    },

    async deleteThread(tid) {
      if (!confirm('确定删除该会话？删除后不可恢复。')) return;
      const res = await fetch('/api/chat/threads/' + tid, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      if (res.ok || res.status === 404) {
        if (this.threadId === tid) {
          this.threadId = null;
          localStorage.removeItem('travel_thread_id');
          this.messages = [];
          this.plan = null;
        }
        await this.loadThreads();
      }
    },

    async uploadDoc(e) {
      const file = e.target && e.target.files && e.target.files[0];
      e.target.value = '';
      if (!file) return;
      if (this.streaming) { alert('小T正在回答中，请稍后再上传'); return; }
      const form = new FormData();
      form.append('file', file);
      try {
        const res = await fetch('/api/chat/upload', {
          method: 'POST',
          body: form,
          credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          alert((data.error && data.error.message) || '文件解析失败，请检查格式');
          return;
        }
        // 提取出的行程要点（非全文）直接作为本轮规划请求，随对话流生成行程
        this.input = '我从文档（' + (data.filename || file.name) + '）中提炼了以下行程要点，'
          + '请基于这些要点帮我规划行程：\n\n【提炼要点】\n'
          + (data.text || '')
          + '\n【要点结束】';
        this.send();
      } catch {
        alert('网络错误，文件上传失败');
      }
    },

    useSuggestion(text) {
      this.input = text;
      this.send();
    },

    autoGrow(el) {
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 160) + 'px';
    },

    resetInputHeight() {
      const el = this.$el && this.$el.querySelector('textarea');
      if (el) this.autoGrow(el);
    },

    onScroll() {
      const el = this.$refs.scroller;
      if (!el) return;
      this.stickBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    },

    scrollDown() {
      if (this.stickBottom) {
        this.$nextTick(() => {
          const el = this.$refs.scroller;
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    },

    renderMd(text) {
      if (!text) return '';
      // CDN 不可用时降级为转义纯文本（保留换行）
      if (!window.marked) return window.escapeHtml(text).replace(/\n/g, '<br>');
      const html = marked.parse(text, { async: false });
      return window.DOMPurify ? DOMPurify.sanitize(html) : html;
    },

    escapeHtml(s) {
      return window.escapeHtml(s);
    },

    _setStep(name, state) {
      const step = this.steps.find((s) => s.name === name);
      if (step) step.state = state;
    },

    async send() {
      const text = this.input.trim();
      if (!text || this.streaming) return;
      this.input = '';
      this.$nextTick(() => this.resetInputHeight());

      this.messages.push({ id: Date.now(), role: 'user', content: text });
      this.streaming = true;
      this.streamingText = '';
      this.plan = null;
      this.steps.forEach((s) => (s.state = 'idle'));
      this._setStep('intent', 'active');
      this.scrollDown();

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, thread_id: this.threadId }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          this._finishWith(
            (err.error && err.error.message) || `请求失败（${res.status}），请稍后重试`
          );
          return;
        }
        await this._consume(res.body.getReader());
      } catch {
        this._finishWith('网络连接中断，请检查服务后重试');
      }
    },

    async _consume(reader) {
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) this._handleFrame(frame);
      }
      this._finalizeStream();
    },

    _handleFrame(frame) {
      let event = '';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!event || !data) return;
      let payload = {};
      try {
        payload = JSON.parse(data);
      } catch {
        return;
      }
      this._dispatch(event, payload);
    },

    _dispatch(event, d) {
      switch (event) {
        case 'status':
          if (d.thread_id) {
            this.threadId = d.thread_id;
            localStorage.setItem('travel_thread_id', d.thread_id);
          }
          this._setStep('intent', 'done');
          break;
        case 'clarify':
          this._setStep('intent', 'done');
          this.streamingText = d.question || '请补充更多信息～';
          break;
        case 'tool_start':
          this._setStep('search', 'active');
          break;
        case 'tool_end':
          if (d.tool && d.tool.includes('web')) this._setStep('search', 'done');
          break;
        case 'plan':
          this._setStep('search', 'done');
          this._setStep('compose', 'done');
          this._setStep('validate', 'done');
          this.plan = d;
          this.scrollDown();
          break;
        case 'plan_patch':
          this._setStep('compose', 'active');
          this._setStep('validate', 'active');
          break;
        case 'token':
          this.steps.forEach((s) => (s.state = 'done'));
          this.streamingText += d.text || '';
          this.scrollDown();
          break;
        case 'warning':
          if (d.message) this.streamingText += `\n\n> ⚠️ ${d.message}`;
          break;
        case 'error':
          this._finishWith(d.message || '服务暂时不可用，请稍后重试');
          break;
        case 'done':
          this.streaming = false;
          break;
      }
    },

    _finalizeStream() {
      if (this.streamingText) {
        this.messages.push({
          id: Date.now(),
          role: 'assistant',
          content: this.streamingText,
        });
      }
      this.streamingText = '';
      this.streaming = false;
      this.scrollDown();
    },

    _finishWith(text) {
      this.streaming = false;
      this.streamingText = '';
      this.messages.push({ id: Date.now(), role: 'assistant', content: '⚠️ ' + text });
      this.scrollDown();
    },
  };
}
