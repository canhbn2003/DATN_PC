document.addEventListener("DOMContentLoaded", function () {
  function forceSidebarWhiteText() {
    var selectors = [
      ".main-sidebar .mt-2",
      ".main-sidebar .mt-2 *",
      ".main-sidebar .brand-text",
      ".main-sidebar .nav-header",
      ".main-sidebar .nav-link",
      ".main-sidebar .nav-link p",
      ".main-sidebar .nav-link span",
      ".main-sidebar .nav-link i",
      ".main-sidebar .nav-link .nav-icon",
      ".main-sidebar .nav-link .right",
      ".main-sidebar .user-panel a",
      ".main-sidebar .nav-treeview .nav-link",
      ".main-sidebar .nav-treeview .nav-link p",
      ".main-sidebar .nav-treeview .nav-link i"
    ];

    selectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el) {
        el.style.setProperty("color", "#ffffff", "important");
        el.style.setProperty("fill", "#ffffff", "important");
        el.style.setProperty("-webkit-text-fill-color", "#ffffff", "important");
        el.style.setProperty("opacity", "1", "important");
      });
    });
  }

  forceSidebarWhiteText();

  var brandLink = document.querySelector("a.brand-link");
  if (brandLink) {
    brandLink.setAttribute("href", "/");

    // In case Jazzmin injects nested clickable elements, keep all logo clicks going home.
    var nestedLinks = brandLink.querySelectorAll("a");
    nestedLinks.forEach(function (link) {
      link.setAttribute("href", "/");
    });
  }

  var dictionary = {
    "Dashboard": "Bảng thống kê",
    "Home": "Trang chủ",
    "Add": "Thêm",
    "Change": "Sửa",
    "Delete": "Xóa",
    "Delete selected": "Xóa mục đã chọn",
    "History": "Lịch sử",
    "Search": "Tìm kiếm",
    "Log out": "Đăng xuất",
    "View site": "Xem trang",
    "Recent actions": "Hoạt động gần đây",
    "My actions": "Hoạt động của tôi",
    "Administration": "Quản trị",
    "Actions": "Thao tác",
    "Go": "Thực hiện",
    "Save": "Lưu",
    "Save and continue editing": "Lưu và tiếp tục chỉnh sửa",
    "Save and add another": "Lưu và thêm mới",
    "Save as new": "Lưu thành bản sao mới",
    "Close": "Đóng",
    "Filter": "Lọc",
    "All": "Tất cả",
    "Yes": "Có",
    "No": "Không",
    "Unknown": "Không xác định",
    "No date": "Không có ngày",
    "Select all": "Chọn tất cả",
    "Deselect all": "Bỏ chọn tất cả",
    "Choose": "Chọn",
    "Choose all": "Chọn tất cả",
    "Remove": "Xóa",
    "None available": "Không có dữ liệu",
    "This field is required.": "Trường này bắt buộc.",
  };

  function translateText(value) {
    if (!value) return value;
    var trimmed = value.trim();
    if (!trimmed) return value;
    return dictionary[trimmed] || value;
  }

  function translateNode(root) {
    var textWalker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var textNode;
    while ((textNode = textWalker.nextNode())) {
      var translated = translateText(textNode.nodeValue);
      if (translated !== textNode.nodeValue) {
        textNode.nodeValue = translated;
      }
    }

    var attrs = root.querySelectorAll("input, button, a, label, option, textarea, select");
    attrs.forEach(function (el) {
      ["placeholder", "title", "aria-label", "value"].forEach(function (attr) {
        var current = el.getAttribute(attr);
        if (!current) return;
        var translated = translateText(current);
        if (translated !== current) {
          el.setAttribute(attr, translated);
        }
      });
    });
  }

  translateNode(document.body);

  var observer = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(function (node) {
        if (node && node.nodeType === Node.ELEMENT_NODE) {
          translateNode(node);
        }
      });
    });

    forceSidebarWhiteText();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  document.body.addEventListener("change", function (event) {
    var input = event.target;
    if (!input || input.tagName !== "INPUT" || input.type !== "file") return;
    if (!input.name || !/\-image_file$/.test(input.name)) return;

    var row = input.closest("tr") || input.closest(".inline-related");
    if (!row) return;

    var preview = row.querySelector("[data-product-image-preview]");
    if (!preview) return;

    var file = input.files && input.files[0];
    var originalSrc = preview.getAttribute("data-original-src") || "";

    if (!file) {
      if (originalSrc) {
        preview.innerHTML = '<img src="' + originalSrc + '" alt="Ảnh" style="width:96px;height:96px;object-fit:cover;border-radius:8px;border:1px solid #e5e7eb;background:#fff;" />';
      } else {
        preview.innerHTML = 'Chưa có ảnh';
      }
      return;
    }

    var objectUrl = URL.createObjectURL(file);
    preview.setAttribute("data-original-src", originalSrc || objectUrl);
    preview.innerHTML = '<img src="' + objectUrl + '" alt="Ảnh xem trước" style="width:96px;height:96px;object-fit:cover;border-radius:8px;border:1px solid #e5e7eb;background:#fff;" />';
  });
});
