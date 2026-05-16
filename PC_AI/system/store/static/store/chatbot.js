(function () {
  if (window.__PCAI_CHATBOT_LOADED__) return;
  window.__PCAI_CHATBOT_LOADED__ = true;

  const apiUrl = "/api/chat/data/";
  const STORAGE_KEY = "pcAiChatHistory";
  const HISTORY_LIMIT = 60;

  const QUICK_PROMPTS = [
    "Tư vấn PC gaming 15 triệu",
    "Build máy học tập dưới 30 triệu",
    "Gợi ý cấu hình đồ hoạ",
  ];

  let isOpen = false;
  let isSending = false;
  let history = [];
  let lastSuggestedItems = [];
  let lastSuggestedTotalPrice = 0;
  let lastSuggestedConfig = null;

  const formatMoney = (amount) => new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 }).format(Number(amount || 0));

  const escapeHtml = (value) =>
    String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  const saveHistory = () => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-HISTORY_LIMIT)));
    } catch (error) {
      console.warn("Không thể lưu lịch sử chat:", error);
    }
  };

  const loadHistory = () => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;

      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;

      history = parsed
        .filter((item) => item && (item.from === "user" || item.from === "bot"))
        .map((item) => ({
          text: String(item.text || "").trim(),
          from: item.from,
          suggestedItems: Array.isArray(item.suggestedItems) ? item.suggestedItems : [],
          totalPrice: Number(item.totalPrice || 0),
          config: item.config && typeof item.config === "object" ? item.config : null,
        }))
        .filter((item) => item.text)
        .slice(-HISTORY_LIMIT);
    } catch (error) {
      history = [];
    }
  };

  const persistMessage = (text, from, suggestedItems = [], totalPrice = 0, config = null) => {
    history.push({ text, from, suggestedItems, totalPrice, config });
    if (history.length > HISTORY_LIMIT) {
      history = history.slice(-HISTORY_LIMIT);
    }
    saveHistory();
  };

  const clearHistory = () => {
    history = [];
    lastSuggestedItems = [];
    lastSuggestedTotalPrice = 0;
    lastSuggestedConfig = null;
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      console.warn("Không thể xóa lịch sử chat:", error);
    }
  };

  const createEl = (tag, attrs = {}, children = []) => {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === "class") {
        el.className = value;
      } else if (key === "html") {
        el.innerHTML = value;
      } else if (key.startsWith("data-")) {
        el.setAttribute(key, value);
      } else {
        el.setAttribute(key, value);
      }
    });
    children.forEach((child) => el.appendChild(child));
    return el;
  };

  const root = createEl("div", { id: "chatbot-root" });
  root.innerHTML = `
    <div id="chatbot-backdrop" aria-hidden="true"></div>
    <button id="chatbot-fab" type="button" aria-label="Mở trợ lý PC AI">
      <span class="chatbot-fab-icon"><i class="bi bi-chat-dots-fill"></i></span>
      <span class="chatbot-fab-pulse"></span>
    </button>

    <section id="chatbot-widget" aria-hidden="true">
      <header id="chatbot-header">
        <div class="chatbot-header-main">
          <div class="chatbot-avatar" aria-hidden="true">
            <span>AI</span>
          </div>
          <div class="chatbot-header-copy">
            <div class="chatbot-title-row">
              <h3>Tư vấn PC AI</h3>
              <span class="chatbot-status"><span class="dot"></span>Online</span>
            </div>
            
          </div>
        </div>
        <div class="chatbot-header-actions">
          <button type="button" id="chatbot-clear" class="chatbot-icon-btn" aria-label="Xóa lịch sử chat">
            <i class="bi bi-arrow-counterclockwise"></i>
          </button>
          <button type="button" id="chatbot-close" class="chatbot-icon-btn chatbot-close-btn" aria-label="Đóng chat">
            <i class="bi bi-x-lg"></i>
          </button>
        </div>
      </header>

      <div class="chatbot-body">
        <div class="chatbot-surface">

          <div id="chatbot-quick-actions" class="chatbot-quick-actions"></div>
          <div id="chatbot-messages" class="chatbot-messages" aria-live="polite"></div>
          <div id="chatbot-typing" class="chatbot-typing" hidden>
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>

      <form id="chatbot-composer" autocomplete="off">
        <div class="chatbot-input-wrap">
          <i class="bi bi-search chatbot-input-icon" aria-hidden="true"></i>
          <input id="chatbot-input" type="text" placeholder="Nhập câu hỏi... ví dụ: build PC gaming 20 triệu" />
        </div>
        <button id="chatbot-send" type="submit">
          <i class="bi bi-send-fill"></i>
          <span>Gửi</span>
        </button>
      </form>
    </section>
  `;

  document.body.appendChild(root);

  const backdrop = root.querySelector("#chatbot-backdrop");
  const fab = root.querySelector("#chatbot-fab");
  const widget = root.querySelector("#chatbot-widget");
  const closeBtn = root.querySelector("#chatbot-close");
  const clearBtn = root.querySelector("#chatbot-clear");
  const quickActions = root.querySelector("#chatbot-quick-actions");
  const messages = root.querySelector("#chatbot-messages");
  const typing = root.querySelector("#chatbot-typing");
  const composer = root.querySelector("#chatbot-composer");
  const input = root.querySelector("#chatbot-input");
  const sendBtn = root.querySelector("#chatbot-send");

  QUICK_PROMPTS.forEach((prompt) => {
    const chip = createEl("button", { type: "button", class: "chatbot-chip", title: prompt, html: prompt });
    chip.addEventListener("click", () => {
      input.value = prompt;
      input.focus();
      sendQuestion(prompt);
    });
    quickActions.appendChild(chip);
  });

  const setTyping = (state) => {
    typing.hidden = !state;
    root.classList.toggle("is-loading", state);
    sendBtn.disabled = state;
    input.disabled = state;
  };

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  };

  const renderSuggestions = (messageEl, suggestedItems = [], totalPrice = 0, config = null) => {
    const items = Array.isArray(suggestedItems) ? suggestedItems : [];
    const hasConfig = config && typeof config === "object" && Object.keys(config).length > 0;

    if (!items.length && !hasConfig) return;

    const panel = createEl("div", { class: "chatbot-suggestion-panel" });

    if (items.length) {
      const summary = createEl("div", { class: "chatbot-suggestion-summary" });
      summary.innerHTML = `
        <div>
          <span class="summary-label">Tổng gợi ý</span>
          <strong>${escapeHtml(formatMoney(totalPrice))} đ</strong>
        </div>
        <div class="summary-badge"><i class="bi bi-stars"></i> Đề xuất tối ưu</div>
      `;
      panel.appendChild(summary);

      const list = createEl("div", { class: "chatbot-product-list" });
      items.forEach((product) => {
        const card = createEl("article", { class: "chatbot-product-card" });
        card.innerHTML = `
          <div class="chatbot-product-top">
            <a class="chatbot-product-name" href="${escapeHtml(product.url || "#")}">${escapeHtml(product.name)}</a>
            <span class="chatbot-product-stock">Còn ${escapeHtml(product.stock ?? 0)}</span>
          </div>
          <div class="chatbot-product-meta">${escapeHtml(product.brand || "Không rõ thương hiệu")}</div>
          <div class="chatbot-product-bottom">
            <div class="chatbot-product-price">${escapeHtml(formatMoney(product.price))} đ</div>
            <button type="button" class="chatbot-add-btn" data-product-id="${escapeHtml(product.id)}">
              <i class="bi bi-plus-lg"></i> Thêm
            </button>
          </div>
        `;
        list.appendChild(card);
      });
      panel.appendChild(list);

      const addAllWrap = createEl("div", { class: "chatbot-add-all-wrap" });
      const addAllBtn = createEl("button", {
        type: "button",
        class: "chatbot-add-all-btn",
        html: '<i class="bi bi-cart-check"></i> Thêm tất cả vào giỏ',
      });
      addAllBtn.addEventListener("click", () => addAllToCart(addAllBtn));
      addAllWrap.appendChild(addAllBtn);
      panel.appendChild(addAllWrap);

      panel.querySelectorAll(".chatbot-add-btn").forEach((btn) => {
        btn.addEventListener("click", () => addProductToCart(Number(btn.dataset.productId), btn));
      });
    }

    if (hasConfig) {
    //   const configActions = createEl("div", { class: "chatbot-config-actions" });
    //   const useConfigBtn = createEl("button", {
    //     type: "button",
    //     class: "chatbot-use-config-btn",
    //     html: '<i class="bi bi-lightning-charge-fill"></i> Dùng cấu hình này',
    //   });

      useConfigBtn.addEventListener("click", async () => {
        useConfigBtn.disabled = true;
        const original = useConfigBtn.innerHTML;
        useConfigBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Đang thêm...';

        try {
          const cart_items = Object.values(config).map((pid) => ({ id: pid, quantity: 1 }));
          const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value || document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || "";

          const response = await fetch("/api/cart/save-to-session/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({ cart_items, sync_db: true }),
          });

          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(payload.error || "Không thể lưu cấu hình vào giỏ hàng.");
          }

          useConfigBtn.innerHTML = '<i class="bi bi-check2-circle"></i> Đã thêm vào giỏ';
        } catch (error) {
          useConfigBtn.innerHTML = '<i class="bi bi-x-circle"></i> Lỗi, thử lại';
        } finally {
          setTimeout(() => {
            useConfigBtn.innerHTML = original;
            useConfigBtn.disabled = false;
          }, 1800);
        }
      });

      configActions.appendChild(useConfigBtn);
      panel.appendChild(configActions);
    }

    messageEl.appendChild(panel);
  };

  const renderMessage = (message) => {
    const article = createEl("article", { class: `chatbot-message is-${message.from}` });
    const meta = createEl("div", { class: "chatbot-message-meta" });
    meta.textContent = message.from === "user" ? "Bạn" : "PC AI";

    const bubble = createEl("div", { class: "chatbot-message-bubble" });
    bubble.innerHTML = escapeHtml(message.text).replaceAll("\n", "<br />");

    article.appendChild(meta);
    article.appendChild(bubble);
    renderSuggestions(article, message.suggestedItems, message.totalPrice, message.config);
    messages.appendChild(article);
  };

  const renderMessages = () => {
    messages.innerHTML = "";

    if (!history.length) {
      const welcome = createEl("div", { class: "chatbot-empty-state" });
      welcome.innerHTML = `
        <div class="chatbot-empty-icon"><i class="bi bi-magic"></i></div>
        <h4>Xin chào, mình là PC AI</h4>
        <p>Hãy mô tả nhu cầu, ngân sách hoặc mục đích sử dụng, mình sẽ gợi ý cấu hình và sản phẩm phù hợp nhất.</p>
      `;
      messages.appendChild(welcome);
      scrollToBottom();
      return;
    }

    history.forEach(renderMessage);
    scrollToBottom();
  };

  const addMessage = (from, text, persist = true, suggestedItems = [], totalPrice = 0, config = null) => {
    const normalizedText = String(text || "").trim();
    if (!normalizedText) return;

    if (persist) {
      persistMessage(normalizedText, from, suggestedItems, totalPrice, config);
    }

    renderMessages();
  };

  async function addProductToCart(productId, btn) {
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Đang thêm';

    try {
      const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value || document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || "";

      const response = await fetch("/api/cart/save-to-session/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({ cart_items: [{ id: productId, quantity: 1 }], sync_db: true }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) {
          addMessage("bot", "Vui lòng đăng nhập để thêm sản phẩm vào giỏ hàng.");
        } else {
          throw new Error(payload.error || "Không thể thêm sản phẩm vào giỏ hàng.");
        }
      } else {
        btn.innerHTML = '<i class="bi bi-check2"></i> Đã thêm';
      }
    } catch (error) {
      addMessage("bot", error && error.message ? error.message : "Không thể thêm sản phẩm. Bạn thử lại nhé.");
    } finally {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
      }, 1200);
    }
  }

  async function addAllToCart(btn) {
    if (!lastSuggestedItems.length) return;

    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Đang thêm';

    try {
      const cart_items = lastSuggestedItems.map((item) => ({ id: item.id, quantity: 1 }));
      const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value || document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || "";

      const response = await fetch("/api/cart/save-to-session/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({ cart_items, sync_db: true }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) {
          addMessage("bot", "Vui lòng đăng nhập để thêm sản phẩm vào giỏ hàng.");
        } else {
          throw new Error(payload.error || "Không thể thêm toàn bộ sản phẩm.");
        }
      } else {
        btn.innerHTML = '<i class="bi bi-check2-circle"></i> Đã thêm tất cả';
      }
    } catch (error) {
      addMessage("bot", error && error.message ? error.message : "Không thể thêm sản phẩm. Bạn thử lại nhé.");
    } finally {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
      }, 1200);
    }
  }

  const sendButtonState = (loading) => {
    isSending = Boolean(loading);
    sendBtn.disabled = isSending;
    input.disabled = isSending;
    widget.classList.toggle("is-loading", isSending);
    typing.hidden = !isSending;
  };

  async function sendQuestion(question) {
    const trimmed = String(question || "").trim();
    if (!trimmed || isSending) return;

    lastSuggestedItems = [];
    lastSuggestedTotalPrice = 0;
    lastSuggestedConfig = null;
    addMessage("user", trimmed);
    sendButtonState(true);

    try {
      const response = await fetch(apiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || payload.details || "Không thể kết nối trợ lý AI.");
      }

      lastSuggestedConfig = payload.suggested_config && typeof payload.suggested_config === "object" ? payload.suggested_config : null;
      lastSuggestedItems = Array.isArray(payload.suggested_items) ? payload.suggested_items : [];
      lastSuggestedTotalPrice = Number(payload.suggested_total_price || 0);

      addMessage("bot", payload.answer || "Chưa nhận được câu trả lời. Bạn thử lại nhé.", true, lastSuggestedItems, lastSuggestedTotalPrice, lastSuggestedConfig);
    } catch (error) {
      lastSuggestedItems = [];
      lastSuggestedTotalPrice = 0;
      lastSuggestedConfig = null;
      addMessage("bot", error && error.message ? error.message : "Đang có lỗi kết nối tới trợ lý AI.");
    } finally {
      sendButtonState(false);
      input.value = "";
      input.focus();
    }
  }

  const openChat = () => {
    isOpen = true;
    root.classList.add("is-open");
    widget.setAttribute("aria-hidden", "false");
    backdrop.setAttribute("aria-hidden", "false");
    input.focus();
    scrollToBottom();
  };

  const closeChat = () => {
    isOpen = false;
    root.classList.remove("is-open");
    widget.setAttribute("aria-hidden", "true");
    backdrop.setAttribute("aria-hidden", "true");
  };

  const resetChatHistory = () => {
    clearHistory();
    history = [{ from: "bot", text: "Xin chào! Mình có thể giúp bạn build PC, chọn linh kiện hoặc tư vấn theo ngân sách. Bạn cần gì nào?", suggestedItems: [], totalPrice: 0, config: null }];
    saveHistory();
    renderMessages();
    input.focus();
  };

  loadHistory();
  if (!history.length) {
    history = [{ from: "bot", text: "Xin chào! Mình có thể giúp bạn build PC, chọn linh kiện hoặc tư vấn theo ngân sách. Bạn cần gì nào?", suggestedItems: [], totalPrice: 0, config: null }];
    saveHistory();
  }
  renderMessages();

  fab.addEventListener("click", openChat);
  closeBtn.addEventListener("click", closeChat);
  backdrop.addEventListener("click", closeChat);

  clearBtn.addEventListener("click", () => {
    const confirmed = window.confirm("Xóa toàn bộ lịch sử chat?\nChat sẽ quay về lời chào ban đầu.");
    if (!confirmed) return;
    resetChatHistory();
    openChat();
  });

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendQuestion(input.value);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendQuestion(input.value);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen) {
      closeChat();
    }
  });

  window.pcAiChatbox = {
    open: openChat,
    close: closeChat,
  };
})();