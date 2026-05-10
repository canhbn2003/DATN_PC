
(function(){
const apiUrl = '/api/chat/data/';
// Tạo icon chat nổi
const icon = document.createElement('div');
icon.id = 'chatbot-fab';
icon.innerHTML = '<svg width="48" height="48" viewBox="0 0 48 48"><circle cx="24" cy="24" r="24" fill="#2d7be5"/><text x="50%" y="55%" text-anchor="middle" fill="#fff" font-size="22" font-family="Arial" dy=".3em">💬</text></svg>';
icon.style.position = 'fixed';
icon.style.bottom = '24px';
icon.style.right = '24px';
icon.style.zIndex = '10000';
icon.style.cursor = 'pointer';
icon.style.boxShadow = '0 4px 16px rgba(0,0,0,0.18)';
icon.style.borderRadius = '50%';
icon.style.transition = 'opacity 0.2s';
document.body.appendChild(icon);

// Tạo box chat (ẩn mặc định)
const widget = document.createElement('div');
widget.id = 'chatbot-widget';
widget.style.display = 'none';
widget.innerHTML = `
  <div id="chatbot-header" style="cursor:pointer;">💬 Tư vấn PC AI <span style="float:right;font-size:20px;cursor:pointer;" id="chatbot-close">×</span></div>
  <div id="chatbot-messages"></div>
  <div id="chatbot-input-row">
    <input id="chatbot-input" type="text" placeholder="Nhập câu hỏi..." autocomplete="off" />
    <button id="chatbot-send">Gửi</button>
  </div>
`;
document.body.appendChild(widget);
const messages = widget.querySelector('#chatbot-messages');
const input = widget.querySelector('#chatbot-input');
const sendBtn = widget.querySelector('#chatbot-send');
const closeBtn = widget.querySelector('#chatbot-close');

function addMsg(text, from) {
  const msg = document.createElement('div');
  msg.style.margin = '8px 0';
  msg.style.textAlign = from==='user'?'right':'left';
  msg.innerHTML = `<span style="display:inline-block;max-width:90%;background:${from==='user'?'#2d7be5;color:#fff':'#e9eef6'};padding:8px 12px;border-radius:8px;">${text.replace(/\n/g,'<br>')}</span>`;
  messages.appendChild(msg);
  messages.scrollTop = messages.scrollHeight;
}
function setLoading(loading) {
  sendBtn.disabled = loading;
  input.disabled = loading;
}
function send() {
  const q = input.value.trim();
  if(!q) return;
  addMsg(q, 'user');
  setLoading(true);
  fetch(apiUrl, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({question: q})
  })
  .then(r=>r.json())
  .then(res=>{
    if(res.answer) addMsg(res.answer, 'bot');
    else addMsg('Lỗi: '+(res.error||'Không nhận được phản hồi!'), 'bot');
  })
  .catch(()=>addMsg('Lỗi kết nối server!', 'bot'))
  .finally(()=>{ setLoading(false); input.value=''; input.focus(); });
}
sendBtn.onclick = send;
input.onkeydown = e=>{ if(e.key==='Enter') send(); };

// Sự kiện mở/đóng chat
icon.onclick = function(){
  widget.style.display = 'block';
  icon.style.opacity = '0';
  setTimeout(()=>{icon.style.display='none';},200);
  input.focus();
};
closeBtn.onclick = function(){
  widget.style.display = 'none';
  icon.style.display = 'block';
  setTimeout(()=>{icon.style.opacity='1';},10);
};
})();