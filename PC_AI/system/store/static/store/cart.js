(function () {
  const STORAGE_KEY_PREFIX = "pcai_cart_";
  const LEGACY_STORAGE_KEY = "pcai_cart";
  const USER_KEY_CACHE = "pcai_cart_last_user_key";
  const CART_SYNC_ENDPOINT = "/api/cart/save-to-session/";
  const CART_LOAD_ENDPOINT = "/api/cart/load-from-database/";

  const toNumber = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const normalizeImageUrl = (rawValue) => {
    const value = String(rawValue || "").trim();
    if (!value) return "";

    if (
      value.startsWith("http://") ||
      value.startsWith("https://") ||
      value.startsWith("//") ||
      value.startsWith("/")
    ) {
      return value;
    }

    const normalized = value.replace(/\\/g, "/").replace(/^\/+/, "");
    if (normalized.toLowerCase().startsWith("media/")) {
      return `/${normalized}`;
    }
    return `/media/${normalized}`;
  };

  const getCsrfToken = () => {
    const inputToken = document.querySelector("[name='csrfmiddlewaretoken']")?.value || "";
    if (inputToken) return inputToken;

    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const isUserLoggedIn = () => {
    const header = document.querySelector(".shop-header");
    const userId = Number.parseInt(header?.dataset.userId || "", 10);
    if (Number.isFinite(userId) && userId > 0) {
      return true;
    }

    return header?.dataset.userAuthenticated === "1";
  };

  const getActiveStorageKey = () => {
    const header = document.querySelector(".shop-header");
    const userId = Number.parseInt(header?.dataset.userId || "", 10);
    if (Number.isFinite(userId) && userId > 0) {
      const key = `${STORAGE_KEY_PREFIX}${userId}`;
      try {
        sessionStorage.setItem(USER_KEY_CACHE, key);
      } catch (error) {
        // Ignore sessionStorage write failures.
      }
      return key;
    }

    if (isUserLoggedIn()) {
      try {
        const cachedKey = sessionStorage.getItem(USER_KEY_CACHE);
        if (cachedKey && cachedKey.startsWith(STORAGE_KEY_PREFIX) && cachedKey !== `${STORAGE_KEY_PREFIX}guest`) {
          return cachedKey;
        }
      } catch (error) {
        // Ignore sessionStorage read failures.
      }
    }

    return `${STORAGE_KEY_PREFIX}guest`;
  };

  const readStoredItems = (storageKey) => {
    if (!storageKey) return [];

    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return [];

      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];

      return parsed.map(sanitizeItem).filter(Boolean);
    } catch (error) {
      return [];
    }
  };

  let cartHydratePromise = null;
  const hydrateCartFromDatabase = () => {
    if (!isUserLoggedIn()) {
      return Promise.resolve([]);
    }

    if (cartHydratePromise) {
      return cartHydratePromise;
    }

    cartHydratePromise = fetch(CART_LOAD_ENDPOINT, {
      method: "GET",
      headers: {
        "Content-Type": "application/json"
      }
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error("Failed to load cart from database");
        }
        return response.json();
      })
      .then((payload) => {
        const serverItems = Array.isArray(payload?.cart_items)
          ? payload.cart_items.map(sanitizeItem).filter(Boolean)
          : [];

        localStorage.setItem(getActiveStorageKey(), JSON.stringify(serverItems));
        notifyCartChange();
        return serverItems;
      })
      .catch(() => {
        return [];
      })
      .finally(() => {
        cartHydratePromise = null;
      });

    return cartHydratePromise;
  };

  const syncCartToSession = (items) => {
    if (!isUserLoggedIn()) return;

    const payload = Array.isArray(items) ? items : [];
    const csrfToken = getCsrfToken();

    fetch(CART_SYNC_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken
      },
      body: JSON.stringify({ cart_items: payload })
    }).catch(() => {
      // Keep cart UX local-first even if backend sync fails.
    });
  };

  const migrateLegacyCartIfNeeded = () => {
    const activeKey = getActiveStorageKey();
    const guestKey = `${STORAGE_KEY_PREFIX}guest`;
    if (activeKey === LEGACY_STORAGE_KEY) return;

    try {
      const existingActive = localStorage.getItem(activeKey);
      const legacyRaw = localStorage.getItem(LEGACY_STORAGE_KEY);
      if (!existingActive && legacyRaw) {
        localStorage.setItem(activeKey, legacyRaw);
        localStorage.removeItem(LEGACY_STORAGE_KEY);
      }

      // If user has just logged in and active cart key is still empty,
      // migrate previous guest cart so items are preserved after re-login.
      const isUserKey = activeKey !== guestKey;
      const guestRaw = localStorage.getItem(guestKey);
      const activeRaw = localStorage.getItem(activeKey);
      if (isUserKey && guestRaw && !activeRaw) {
        localStorage.setItem(activeKey, guestRaw);
      }
    } catch (error) {
      // Ignore storage migration errors.
    }
  };

  const openLoginUI = () => {
    const modal = document.getElementById("authModal");
    if (modal) {
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");

      const tabs = modal.querySelectorAll("[data-auth-tab]");
      const panels = modal.querySelectorAll("[data-auth-panel]");
      tabs.forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.authTab === "login");
      });
      panels.forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.authPanel === "login");
      });
      return;
    }

    window.location.href = "/?auth=login";
  };

  const refreshSessionRecommendations = async () => {
    const sections = document.querySelectorAll("[data-session-recommendations]");
    if (!sections.length) return;

    await Promise.all(Array.from(sections).map(async (section) => {
      const grid = section.querySelector("[data-session-rec-grid]");
      if (!grid) return;

      const limit = Number.parseInt(section.dataset.sessionLimit || "8", 10) || 8;

      try {
        const response = await fetch(`/api/recommendations/session/?limit=${encodeURIComponent(limit)}`);
        if (!response.ok) return;

        const payload = await response.json();
        if (typeof payload.items_html === "string") {
          grid.innerHTML = payload.items_html;
        }
      } catch (error) {
        // Keep UI stable even if recommendation refresh fails.
      }
    }));
  };

  const requireLoginForCartAction = () => {
    if (isUserLoggedIn()) return true;
    showToast("Vui lòng đăng nhập để thêm sản phẩm vào giỏ hàng", "error");
    openLoginUI();
    return false;
  };

  const sanitizeItem = (rawItem) => {
    if (!rawItem || typeof rawItem !== "object") return null;

    const id = toNumber(rawItem.id, 0);
    if (!id) return null;

    const status = String(rawItem.status || "Đang kinh doanh");
    const isDiscontinued =
      typeof rawItem.isDiscontinued === "boolean"
        ? rawItem.isDiscontinued
        : status === "Ngừng kinh doanh";

    return {
      id,
      name: String(rawItem.name || "San pham"),
      brand: String(rawItem.brand || "N/A"),
      category: String(rawItem.category || "Khác"),
      image: normalizeImageUrl(rawItem.image),
      price: toNumber(rawItem.price, 0),
      quantity: Math.max(1, Math.floor(toNumber(rawItem.quantity, 1))),
      status,
      isDiscontinued
    };
  };

  const readCart = () => {
    try {
      migrateLegacyCartIfNeeded();
      return readStoredItems(getActiveStorageKey());
    } catch (error) {
      return [];
    }
  };

  const notifyCartChange = () => {
    document.dispatchEvent(new CustomEvent("pcai:cart-changed"));
  };

  const writeCart = (items) => {
    localStorage.setItem(getActiveStorageKey(), JSON.stringify(items));
    syncCartToSession(items);
    notifyCartChange();
  };

  const getCount = () => readCart().reduce((sum, item) => sum + item.quantity, 0);

  const updateBadge = () => {
    const badges = document.querySelectorAll(".cart-badge");
    if (!badges.length) return;

    if (!isUserLoggedIn()) {
      badges.forEach((badge) => {
        badge.textContent = "0";
        badge.style.display = "none";
      });
      return;
    }

    const count = getCount();
    const text = count > 99 ? "99+" : String(count);

    badges.forEach((badge) => {
      badge.textContent = text;
      badge.style.display = count > 0 ? "inline-flex" : "none";
    });
  };

  const addItem = (item, quantity = 1) => {
    const incoming = sanitizeItem({ ...item, quantity });
    if (!incoming) return;

    const cartItems = readCart();
    const existingIndex = cartItems.findIndex((cartItem) => cartItem.id === incoming.id);

    if (existingIndex >= 0) {
      cartItems[existingIndex].quantity += Math.max(1, Math.floor(quantity));
      if (!cartItems[existingIndex].image && incoming.image) {
        cartItems[existingIndex].image = incoming.image;
      }
      if (incoming.price) {
        cartItems[existingIndex].price = incoming.price;
      }
    } else {
      cartItems.push(incoming);
    }

    writeCart(cartItems);
  };

  const updateQuantity = (productId, quantity) => {
    const targetId = toNumber(productId, 0);
    if (!targetId) return;

    const cartItems = readCart();
    const itemIndex = cartItems.findIndex((item) => item.id === targetId);
    if (itemIndex < 0) return;

    const nextQuantity = Math.floor(toNumber(quantity, 1));
    if (nextQuantity <= 0) {
      cartItems.splice(itemIndex, 1);
    } else {
      cartItems[itemIndex].quantity = nextQuantity;
    }

    writeCart(cartItems);
  };

  const removeItem = (productId) => {
    const targetId = toNumber(productId, 0);
    if (!targetId) return;

    const nextItems = readCart().filter((item) => item.id !== targetId);
    writeCart(nextItems);
  };

  const clearCart = () => {
    writeCart([]);
  };

  const formatPrice = (amount) => {
    return new Intl.NumberFormat("vi-VN", {
      maximumFractionDigits: 0
    }).format(toNumber(amount, 0));
  };

  const showToast = (message, type = "success") => {
    const text = String(message || "").trim();
    if (!text) return;

    let host = document.querySelector("[data-cart-toast-host]");
    if (!host) {
      host = document.createElement("div");
      host.className = "cart-toast-host";
      host.setAttribute("data-cart-toast-host", "true");
      document.body.appendChild(host);
    }

    const toast = document.createElement("div");
    toast.className = `cart-toast ${type === "error" ? "error" : "success"}`;
    toast.textContent = text;
    host.appendChild(toast);

    // Trigger transition after insertion so the browser can animate it.
    requestAnimationFrame(() => {
      toast.classList.add("show");
    });

    window.setTimeout(() => {
      toast.classList.remove("show");
      window.setTimeout(() => {
        toast.remove();
        if (!host.children.length) {
          host.remove();
        }
      }, 220);
    }, 2200);
  };

  window.PCAI_CART = {
    getCart: readCart,
    getCount,
    addItem,
    updateQuantity,
    removeItem,
    clearCart,
    formatPrice,
    showToast,
    updateBadge,
    requireLoginForCartAction,
    hydrateCartFromDatabase,
    storageKey: getActiveStorageKey,
    getStorageKey: getActiveStorageKey
  };

  document.addEventListener("DOMContentLoaded", () => {
    hydrateCartFromDatabase().finally(() => {
      updateBadge();
    });
    refreshSessionRecommendations();
  });
  document.addEventListener("pcai:cart-changed", updateBadge);
  document.addEventListener("pcai:behavior-updated", refreshSessionRecommendations);
  window.addEventListener("storage", updateBadge);

  const buildProductPayloadFromDataset = (dataset) => {
    const productId = toNumber(dataset.productId, 0);
    if (!productId) return null;

    const status = String(dataset.productStatus || "Đang kinh doanh");

    return {
      id: productId,
      name: String(dataset.productName || "Sản phẩm"),
      brand: String(dataset.productBrand || "N/A"),
      category: String(dataset.productCategory || "Khác"),
      price: toNumber(dataset.productPrice, 0),
      image: normalizeImageUrl(dataset.productImage),
      status,
      isDiscontinued: status === "Ngừng kinh doanh"
    };
  };

  const trackBehavior = (productId, action) => {
    if (!productId || !action) return;

    return fetch("/api/behavior/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        product_id: productId,
        action
      })
    }).catch(() => {
      // Tracking failure should never block cart UX.
    });
  };

  document.addEventListener("click", (event) => {
    const addCartButton = event.target.closest("[data-card-add-cart]");
    if (addCartButton) {
      event.preventDefault();
      event.stopPropagation();

      if (!requireLoginForCartAction()) return;

      const productPayload = buildProductPayloadFromDataset(addCartButton.dataset);
      if (!productPayload) return;

      addItem(productPayload, 1);
      trackBehavior(productPayload.id, "add_to_cart")?.finally(() => {
        refreshSessionRecommendations();
      });
      showToast("Đã thêm sản phẩm vào giỏ hàng", "success");
      return;
    }

    const card = event.target.closest("[data-product-card]");
    if (!card) return;

    const interactive = event.target.closest("button, a, input, select, textarea, label");
    if (interactive) return;

    const detailUrl = card.dataset.productDetailUrl;
    if (!detailUrl) return;
    window.location.href = detailUrl;
  });

  document.addEventListener("keydown", (event) => {
    const card = event.target.closest("[data-product-card]");
    if (!card) return;

    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();

    const detailUrl = card.dataset.productDetailUrl;
    if (!detailUrl) return;
    window.location.href = detailUrl;
  });
})();
