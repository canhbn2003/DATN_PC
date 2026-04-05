document.addEventListener("DOMContentLoaded", function () {
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
  });
  observer.observe(document.body, { childList: true, subtree: true });
});
