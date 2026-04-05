(function () {
  const STORAGE_KEY = "pcai_cart";

  const toNumber = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const isUserLoggedIn = () => {
    const header = document.querySelector(".shop-header");
    return header?.dataset.userAuthenticated === "1";
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

    return {
      id,
      name: String(rawItem.name || "San pham"),
      brand: String(rawItem.brand || "N/A"),
      image: String(rawItem.image || ""),
      price: toNumber(rawItem.price, 0),
      quantity: Math.max(1, Math.floor(toNumber(rawItem.quantity, 1)))
    };
  };

  const readCart = () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];

      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];

      return parsed.map(sanitizeItem).filter(Boolean);
    } catch (error) {
      return [];
    }
  };

  const notifyCartChange = () => {
    document.dispatchEvent(new CustomEvent("pcai:cart-changed"));
  };

  const writeCart = (items) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    notifyCartChange();
  };

  const getCount = () => readCart().reduce((sum, item) => sum + item.quantity, 0);

  const updateBadge = () => {
    const badges = document.querySelectorAll(".cart-badge");
    if (!badges.length) return;

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
    storageKey: STORAGE_KEY
  };

  document.addEventListener("DOMContentLoaded", updateBadge);
  document.addEventListener("pcai:cart-changed", updateBadge);
  window.addEventListener("storage", updateBadge);

  const buildProductPayloadFromDataset = (dataset) => {
    const productId = toNumber(dataset.productId, 0);
    if (!productId) return null;

    return {
      id: productId,
      name: String(dataset.productName || "Sản phẩm"),
      brand: String(dataset.productBrand || "N/A"),
      price: toNumber(dataset.productPrice, 0),
      image: String(dataset.productImage || "")
    };
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
