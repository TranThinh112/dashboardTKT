const sectionTitles = {
  orders: {
    page: "Dashboard tuyển dụng",
    panel: "Quản lý đơn tuyển dụng",
  },
  candidates: {
    page: "Quản lý ứng viên",
    panel: "Danh sách ứng viên",
  },
  collaborators: {
    page: "Quản lý cộng tác viên",
    panel: "Danh sách cộng tác viên",
  },
};

let imageDataReady = false;
let editingOrderCode = "";
let editingOrderSnapshot = null;
let editingCandidateId = "";
let editingApplicationId = "";
let editingCandidateSnapshot = null;
let pendingDeleteCode = "";
let pendingDeleteCandidateId = "";
let pendingDeleteCtvId = "";
let currentOrders = [];
let activeSection = localStorage.getItem("activeDashboardSection") || "orders";
let currentCandidates = [];
let currentCtvs = [];
let currentApplications = [];
let currentMetrics = null;
let sectionRenderToken = 0;
let candidatesLoaded = false;
let ctvsLoaded = false;
let activeInterviewApplicationId = "";
const API_CACHE_TTL = 20000;
const ORDER_DETAIL_CACHE_PREFIX = "orderDetail:";
const apiCache = new Map();

function getCachedApi(key) {
  const item = apiCache.get(key);
  if (!item) return null;
  if (Date.now() - item.createdAt > API_CACHE_TTL) {
    apiCache.delete(key);
    return null;
  }
  return item.promise;
}

function loadCachedApi(key, loader) {
  const cached = getCachedApi(key);
  if (cached) return cached;
  const promise = loader().catch((error) => {
    apiCache.delete(key);
    throw error;
  });
  apiCache.set(key, { createdAt: Date.now(), promise });
  return promise;
}

function clearFrontendCache() {
  apiCache.clear();
  candidatesLoaded = false;
  ctvsLoaded = false;
}

function getOrderDetailCacheKey(code) {
  return `${ORDER_DETAIL_CACHE_PREFIX}${code}`;
}

function getPersistedOrderDetail(code, updatedAt = "") {
  if (!code) return null;
  try {
    const cached = JSON.parse(localStorage.getItem(getOrderDetailCacheKey(code)) || "null");
    if (!cached?.order) return null;
    if (updatedAt && cached.updatedAt !== updatedAt) return null;
    return cached.order;
  } catch {
    return null;
  }
}

function persistOrderDetail(order) {
  if (!order?.code) return;
  try {
    localStorage.setItem(
      getOrderDetailCacheKey(order.code),
      JSON.stringify({
        updatedAt: order.updatedAt || "",
        savedAt: Date.now(),
        order,
      }),
    );
  } catch {
    localStorage.removeItem(getOrderDetailCacheKey(order.code));
  }
}

function clearPersistedOrderDetail(code) {
  if (!code) return;
  localStorage.removeItem(getOrderDetailCacheKey(code));
}

function escapeHtml(value) {
  return value
    .toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatShortId(value) {
  return String(value || "").slice(-3);
}

function sanitizeFileName(value) {
  return String(value || "file")
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 140) || "file";
}

function getImageExtension(dataUrl) {
  const match = String(dataUrl || "").match(/^data:image\/([a-zA-Z0-9.+-]+);/);
  if (!match) return "png";
  return match[1].toLowerCase().replace("jpeg", "jpg").replace("svg+xml", "svg");
}

function downloadDataUrl(dataUrl, fileName) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function showRequiredFieldsMessage(messageElement, fields) {
  messageElement.textContent = `Vui lòng nhập/chọn: ${fields.join(", ")}.`;
  messageElement.className = "form-message error";
}

function getMissingOrderFields(form) {
  const fields = [];
  if (!form.elements.code.value.trim()) fields.push("Mã đơn");
  if (!form.elements.title.value.trim()) fields.push("Vị trí tuyển dụng");
  if (!form.elements.department.value.trim()) fields.push("Nhóm ngành / phòng ban");
  if (!form.elements.headcount.value.trim()) fields.push("Số lượng cần tuyển");
  return fields;
}

function getMissingCandidateFields(form, selectedOrder, selectedCtv) {
  const fields = [];
  if (!form.elements.fullName.value.trim()) fields.push("Họ tên");
  if (!selectedOrder?.id) fields.push("Đơn");
  if (!selectedCtv?.id) fields.push("CTV");
  return fields;
}

function normalizePhoneKey(value) {
  return String(value || "").trim();
}

function hasValidPhoneCharacters(value) {
  return /^\d+$/.test(String(value || "").trim());
}

function isValidVietnamMobile(value) {
  return hasValidPhoneCharacters(value) && /^0(?:3|5|7|8|9)\d{8}$/.test(normalizePhoneKey(value));
}

function isValidEmail(value) {
  const email = String(value || "").trim();
  if (email.split("@").length !== 2 || /\s/.test(email) || !email.toLowerCase().endsWith(".com") || email.includes("..")) {
    return false;
  }
  const [local, domain] = email.split("@");
  if (
    !local ||
    !domain ||
    local.startsWith(".") ||
    local.endsWith(".") ||
    !/^[A-Za-z0-9._-]+$/.test(local) ||
    !/^[A-Za-z0-9.-]+$/.test(domain)
  ) {
    return false;
  }
  return domain.split(".").every((label) => label && !label.startsWith("-") && !label.endsWith("-"));
}

function normalizeComparableValue(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function normalizeComparableRecord(record) {
  return Object.fromEntries(
    Object.entries(record).map(([key, value]) => [key, normalizeComparableValue(value)]),
  );
}

function hasComparableChanges(before, after) {
  const normalizedBefore = normalizeComparableRecord(before || {});
  const normalizedAfter = normalizeComparableRecord(after || {});
  const keys = new Set([...Object.keys(normalizedBefore), ...Object.keys(normalizedAfter)]);
  return [...keys].some((key) => normalizedBefore[key] !== normalizedAfter[key]);
}

function normalizeJsonComparable(value) {
  try {
    return JSON.stringify(JSON.parse(value || "{}"));
  } catch {
    return normalizeComparableValue(value);
  }
}

function buildOrderEditSnapshot(payload) {
  return {
    code: payload.code,
    title: payload.title,
    department: payload.department,
    industry: payload.industry,
    industries: Array.isArray(payload.industries) ? payload.industries.join("|") : payload.industries,
    location: payload.location,
    headcount: payload.headcount,
    status: payload.status,
    rawText: payload.rawText,
    hasImage: payload.hasImage,
    hasImageData: payload.hasImageData,
    textUpFb: payload.textUpFb,
    jobJson: normalizeJsonComparable(payload.jobJson),
    imageDataUrl: payload.imageDataUrl,
  };
}

async function loadDashboard() {
  const status = "all";
  const search = "";
  const params = new URLSearchParams({ status, search });
  return loadCachedApi(`dashboard:${params.toString()}`, async () => {
    const response = await fetch(`/api/dashboard?${params.toString()}`);

    if (!response.ok) {
      throw new Error("Không thể tải dữ liệu dashboard");
    }

    return response.json();
  });
}

async function loadBootstrap() {
  const search = "";
  const params = new URLSearchParams({ status: "all", search });
  return loadCachedApi(`bootstrap:${params.toString()}`, async () => {
    const response = await fetch(`/api/bootstrap?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "Không thể tải dữ liệu dashboard");
    return data;
  });
}

async function loadOrderDetail(code, updatedAt = "") {
  const persisted = getPersistedOrderDetail(code, updatedAt);
  if (persisted) return persisted;
  const params = new URLSearchParams({ code });
  return loadCachedApi(`order-detail:${params.toString()}`, async () => {
    const response = await fetch(`/api/order-detail?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "Không thể tải chi tiết đơn");
    persistOrderDetail(data.order);
    return data.order;
  });
}

async function ensureOrderDetail(order) {
  if (!order?.code) return order;
  if ("imageDataUrl" in order && "rawText" in order && "textUpFb" in order && "jobJson" in order) {
    return order;
  }
  const persisted = getPersistedOrderDetail(order.code, order.updatedAt);
  const detail = persisted || await loadOrderDetail(order.code, order.updatedAt);
  const merged = { ...order, ...detail };
  persistOrderDetail(merged);
  currentOrders = currentOrders.map((item) => (item.code === merged.code ? merged : item));
  return merged;
}

async function createOrder(payload) {
  const response = await fetch("/api/orders", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.message || "Không thể tạo đơn tuyển dụng");
  }

  clearFrontendCache();
  clearPersistedOrderDetail(payload.code);
  return data;
}

async function updateOrder(originalCode, payload) {
  const response = await fetch(`/api/orders/${encodeURIComponent(originalCode)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.message || "Không thể cập nhật đơn tuyển dụng");
  }

  clearFrontendCache();
  clearPersistedOrderDetail(originalCode);
  if (payload.code && payload.code !== originalCode) clearPersistedOrderDetail(payload.code);
  return data;
}

async function deleteOrder(code) {
  const response = await fetch(`/api/orders/${encodeURIComponent(code)}`, {
    method: "DELETE",
  });
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.message || "Không thể xóa đơn tuyển dụng");
  }

  clearFrontendCache();
  clearPersistedOrderDetail(code);
  return data;
}

async function loadCandidates() {
  return loadCachedApi("candidates", async () => {
    const response = await fetch("/api/candidates");
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "Không thể tải ứng viên");
    return data.candidates || [];
  });
}

async function createCandidate(payload) {
  const response = await fetch("/api/candidates", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể tạo ứng viên");
  clearFrontendCache();
  return data;
}

async function updateCandidate(candidateId, payload) {
  const response = await fetch(`/api/candidates/${encodeURIComponent(candidateId)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể cập nhật ứng viên");
  clearFrontendCache();
  return data;
}

async function deleteCandidate(candidateId) {
  const response = await fetch(`/api/candidates/${encodeURIComponent(candidateId)}`, {
    method: "DELETE",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể xóa ứng viên");
  clearFrontendCache();
  return data;
}

async function deleteCtv(ctvId) {
  const response = await fetch(`/api/ctvs/${encodeURIComponent(ctvId)}`, {
    method: "DELETE",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể xóa CTV");
  clearFrontendCache();
  return data;
}

async function createApplication(payload) {
  const response = await fetch("/api/applications", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể gán ứng viên vào đơn");
  clearFrontendCache();
  return data;
}

async function updateApplication(applicationId, payload) {
  const response = await fetch(`/api/applications/${encodeURIComponent(applicationId)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể cập nhật đơn tham gia");
  clearFrontendCache();
  return data;
}

async function loadCtvs() {
  return loadCachedApi("ctvs", async () => {
    const response = await fetch("/api/ctvs");
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "Không thể tải cộng tác viên");
    return data.ctvs || [];
  });
}

async function createCtv(payload) {
  const response = await fetch("/api/ctvs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "Không thể thêm CTV");
  clearFrontendCache();
  return data;
}

async function loadApplications() {
  return loadCachedApi("applications", async () => {
    const response = await fetch("/api/applications");
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "Không thể tải dữ liệu tham gia đơn");
    return data.applications || [];
  });
}

function normalizeText(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
}

function stripEmoji(text) {
  return String(text || "")
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
    .trim();
}

function findValue(text, patterns) {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return normalizeText(match[1] || match[0]);
  }
  return "";
}

function splitIndustries(value) {
  const rawItems = Array.isArray(value)
    ? value
    : String(value || "")
        .replace(/｜/g, "|")
        .replace(/;/g, ",")
        .split(",");
  const industries = [];
  const seen = new Set();
  rawItems.forEach((item) => {
    String(item)
      .split("|")
      .map((part) => part.trim())
      .filter(Boolean)
      .forEach((industry) => {
        const cleanIndustry = cleanIndustryLabel(industry);
        const key = normalizeSearchText(cleanIndustry);
        if (!seen.has(key)) {
          seen.add(key);
          industries.push(cleanIndustry);
        }
      });
  });
  return industries;
}

function cleanIndustryLabel(value) {
  const label = String(value || "")
    .replace(/^[\s\-_/]*(?:jp|jpj|jjp|jpn)\b[\s\-_/]*/i, "")
    .replace(/\b(?:jp|jpj|jjp|jpn)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  const normalized = normalizeSearchText(label);
  if (/xay dung|cot thep|gian giao|cong trinh|be tong|cong truong/.test(normalized)) return "Xây dựng";
  if (/co khi|han|san xuat|factory|may/.test(normalized)) return "Cơ khí/Sản xuất";
  if (/khach san|ve sinh|vstn|don dep/.test(normalized)) return "Khách sạn";
  if (/thuc pham|che bien|food|nong nghiep/.test(normalized)) return "Thực phẩm";
  return label || "Chưa có ngành";
}

function parseRawTextByRules(raw) {
  raw = normalizeText(raw);
  const lines = raw.split(/\r?\n/).map(stripEmoji).filter(Boolean);
  const title = lines[0] || "Đơn tuyển";
  const orderCode = findValue(raw, [/Mã đơn hàng\s*:\s*([^\n]+)/i, /Mã đơn\s*:\s*([^\n]+)/i]) || "Chưa có";
  const location = findValue(raw, [/Tỉnh\s*:\s*([^\n]+)/i, /Nơi Làm Việc\s*([^\n]+)/i, /Nơi làm việc\s*:\s*([^\n]+)/i, /Địa điểm\s*:\s*([^\n]+)/i, /Khu vực\s*:\s*([^\n]+)/i]) || "Chưa rõ";
  const quantity = findValue(raw, [/Tuyển\s*:?\s*([0-9]+)/i, /Số Lượng Tuyển\s*([0-9]+)/i, /Số lượng\s*:\s*([0-9]+)/i]);
  const salaryText = findValue(raw, [/Lương\s*:\s*([^\n]+)/i, /Lương\s*Trợ cấp\s*([^\n]+)/i]) || "Liên hệ";
  const requirement = findValue(raw, [/Yêu cầu\s*:\s*([^\n]+)/i, /Điều Kiện[\s\S]*?\(Ghi chú thêm\)\s*([^\n]+)/i]) || "Liên hệ";
  const work = findValue(raw, [/Nội dung công việc\s*:\s*([^\n]+)/i, /Công việc\s*:\s*([^\n]+)/i]) || title;
  const back = findValue(raw, [/Back\s*:\s*([^\n]+)/i]) || "";
  const company = findValue(raw, [/Tên công ty\s*:?(.+)/i, /Tên Cty[\s\S]*?Vị Trí\s*([^\n]+)/i]) || "";
  const housing = findValue(raw, [/Nhà.*?(?:KTX|ký túc xá|Miễn phí)[^\n]*/i]) || "";
  const interview = findValue(raw, [/Phỏng Vấn[\s\S]*?Hình Thức PV\s*([^\n]+)/i, /Phỏng vấn\s*:\s*([^\n]+)/i]) || "";
  const insurance = findValue(raw, [/Bảo Hiểm[\s\S]*?Phúc Lợi\s*([^\n]+)/i]) || "";
  const workingHours = findValue(raw, [/Giờ Làm[\s\S]*?Ngày Nghỉ\s*([^\n]+)/i, /Giờ làm việc\s*:?\s*([^\n]+)/i]) || "";
  const daysOff = findValue(raw, [/Ngày nghỉ\s*:?\s*([^\n]+)/i]) || "";
  const japaneseMatch = requirement.match(/N[1-5]/i);
  const salaryNumber = salaryText.match(/[\d,.]+/);
  const salaryPeriod = /giờ|gio/i.test(salaryText) ? "giờ" : (/tháng|thang/i.test(salaryText) ? "tháng" : (/năm|nam/i.test(salaryText) ? "năm" : ""));
  const titleParts = title.replace(/-/g, " - ").split("-").map((part) => part.trim()).filter(Boolean);
  const industryText = findValue(raw, [/Nhóm ngành\s*:\s*([^\n]+)/i, /Ngành\s*:\s*([^\n]+)/i]) || titleParts[0] || title;
  const industries = splitIndustries(industryText);
  const industry = industries[0] || industryText;

  return {
    orderCode,
    orderType: "Đơn tuyển",
    industry,
    industries,
    jobTitle: title,
    location,
    company,
    salaryText,
    allowance: "",
    housing,
    insurance,
    workingHours,
    benefit: /thưởng|tăng lương/i.test(salaryText) ? "Có thưởng, tăng lương" : "",
    requirement,
    japaneseLevel: japaneseMatch ? japaneseMatch[0].toUpperCase() : "",
    salaryNumber: salaryNumber ? salaryNumber[0] : "",
    salaryPeriod,
    interview: interview || "Liên hệ",
    quantity: quantity ? Number(quantity) : "",
    daysOff: daysOff || "Liên hệ",
    work,
    back,
    source: {
      image_ocr_used: false,
      pasted_text_used: Boolean(raw),
    },
  };
}

function yenToMan(text) {
  return String(text || "").replace(/([\d.,]+)\s*(y[eê]n|yen)/gi, (_, amount, unit) => {
    const yen = Number(amount.replace(/[,.]/g, ""));
    if (!yen) return `${amount} ${unit}`;
    if (yen >= 1000 && yen < 10000) {
      const sen = yen / 1000;
      return `${Number.isInteger(sen) ? String(sen) : sen.toFixed(1).replace(".", ",").replace(/,0$/, "")}s`;
    }
    if (yen < 10000) return `${amount} ${unit}`;
    const man = yen / 10000;
    return `${Number.isInteger(man) ? String(man) : man.toFixed(1).replace(/\.0$/, "")}M`;
  });
}

function shortenFacebookWords(text) {
  return String(text || "")
    .replace(/k[yỹĩ]\s*s[uư]/gi, "KS")
    .replace(/qu[aả]n\s*l[yý]/gi, "Qly")
    .replace(/y[eê]u\s*c[aầ]u/gi, "yc")
    .replace(/th[uư][oở]ng/gi, "thg")
    .replace(/gi[oờ]/gi, "H")
    .replace(/kinh\s*nghi[eệ]m/gi, "KN");
}

function normalizeJapaneseLevel(text) {
  return String(text || "")
    .replace(/\bnh[aậ]n\s*t[uừ]\s*(N[1-5])\b(?:\s*tr[oở]\s*l[eê]n)?/gi, "$1 trở lên")
    .replace(/\bt[uừ]\s*(N[1-5])\b(?:\s*tr[oở]\s*l[eê]n)?/gi, "$1 trở lên")
    .replace(/\b(N[1-5])\b(?!\s*tr[oở]\s*l[eê]n)/gi, "$1 trở lên");
}

function cleanJobTitle(text) {
  const title = String(text || "")
    .replace(/🇯🇵/g, "")
    .replace(/\bjp\b/gi, "")
    .replace(/^\s*(?:jp\s*)?(?:tokutei|tokutei\s*-\s*t[aá]i\s*nh[aậ]t)\s*/i, "")
    .replace(/^\s*đ[oơ]n\s+(?:tokutei|tuy[eể]n)\s*/i, "")
    .trim();
  if (/kh[aá]ch\s*s[aạ]n/i.test(title) && /(d[oọ]n\s*d[eẹ]p|v[eệ]\s*sinh|ph[oò]ng)/i.test(title)) return "VSTN";
  if (/c[oố]t\s*th[eé]p/i.test(title)) return "Cốt thép";
  if (/(k[yỹĩ]\s*s[uư]|KS|qu[aả]n\s*l[yý]|Qly)/i.test(title) && /c[oô]ng\s*tr[iì]nh/i.test(title)) return "KS Qly công trình";
  return title;
}

function splitRequirementNote(text) {
  const match = String(text || "").match(/^(.*?)\s*[\(（]\s*(.*?)\s*[\)）]\s*$/);
  if (!match) return { main: text, note: "" };
  const note = match[2]
    .replace(/^(u+u|ưu)\s*ti[eê]n\s+c[oó]\s+KN.*$/i, "Ưu tiên có KN")
    .replace(/^u+u\s*ti[eê]n\s*/i, "Ưu tiên ")
    .replace(/^ưu\s*ti[eê]n\s*/i, "Ưu tiên ")
    .trim();
  return { main: match[1].trim(), note };
}

function shortenLocation(text) {
  const parts = String(text || "").split("-").map((part) => part.trim()).filter(Boolean);
  return parts.length >= 4 ? `${parts.slice(0, 3).join(", ")}...` : text;
}

function shortenSalaryNote(text) {
  return String(text || "")
    .replace(/\(\s*c[oó]\s*t[aă]ng\s*ca,\s*tr[eê]n\s*50\s*ti[eế]ng\s*1\s*th[aá]ng,\s*c[oó]\s*l[aà]m\s*đ[eê]m\s*\)/gi, "(TCA 50H/1 tháng, làm đêm)")
    .replace(/\(\s*kh[oô]ng\s*chuy[eể]n\s*đ[oổ]i\s*\)/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function shortenRequirementNote(requirement) {
  if (/N4/i.test(requirement) && /N5/i.test(requirement) && /c[uứ]ng/i.test(requirement)) return "N5 cứng trở lên";
  return requirement;
}

function splitSalaryExtra(text) {
  const match = String(text || "").match(/^(.*?)\s*\((TCA[^)]*l[aà]m\s*đ[eê]m)\)\s*(.*)$/i);
  if (!match) return { salary: text, extra: "" };
  return { salary: `${match[1]} ${match[3]}`.trim(), extra: match[2].trim() };
}

function makeShortText(data) {
  const salaryParts = splitSalaryExtra(shortenSalaryNote(data.salaryText));
  const salary = shortenFacebookWords(yenToMan(salaryParts.salary))
    .replace(/\s+([.,;:])/g, "$1")
    .replace(/([.,;:])\s*/g, "$1 ")
    .replace(/(\d)\s*([.,])\s*(\d)/g, "$1$2$3")
    .replace(/\s*\/\s*/g, " /")
    .replace(/\s*-\s*/g, " - ")
    .replace(/\s+/g, " ")
    .trim();
  const salaryExtra = shortenFacebookWords(salaryParts.extra).replace(/\s*\/\s*/g, "/").replace(/\s+/g, " ").trim();
  const jobTitle = shortenFacebookWords(cleanJobTitle(data.jobTitle));
  const location = shortenLocation(data.location);
  const requirement = splitRequirementNote(shortenRequirementNote(normalizeJapaneseLevel(shortenFacebookWords(data.requirement))));
  const requirementLine = [`YC: ${requirement.main}`, requirement.note].filter(Boolean).join(" ");
  const detailLine = [salary.replace(/\s*$/, "").replace(/[.!?]$/, ""), salaryExtra, requirementLine].filter(Boolean).join(". ");
  return [jobTitle, location, detailLine].filter(Boolean).map((line) => line.toUpperCase()).join("\n");
}

function buildJobJson(data) {
  const imageAttached = document.getElementById("imagePasteBox")?.querySelector("img") !== null;
  return {
    order_code: data.orderCode,
    order_type: data.orderType,
    industry: data.industry,
    industries: data.industries || splitIndustries(data.industry),
    job_title: data.jobTitle,
    location: data.location,
    company: data.company || "",
    salary_text: data.salaryText,
    salary_number: data.salaryNumber,
    salary_period: data.salaryPeriod,
    allowance: data.allowance,
    housing: data.housing || "",
    benefits: data.benefit,
    requirement: data.requirement,
    japanese_level: data.japaneseLevel,
    interview: data.interview,
    quantity: data.quantity,
    days_off: data.daysOff,
    working_hours: data.workingHours || "",
    insurance_benefits: data.insurance || "",
    job_description: data.work,
    back_fee: data.back,
    chatbot_summary: `Đơn ${data.jobTitle} tại ${data.location}. Công việc: ${data.work}. Lương: ${data.salaryText}. Yêu cầu: ${data.requirement}. Số lượng: ${data.quantity || "liên hệ"}.`,
    image_data_status: imageDataReady ? "đã có data từ ảnh" : "chưa có data từ ảnh",
    image_attached: imageAttached,
    source: data.source || {},
  };
}

function getCreateOrderFormData() {
  const form = document.getElementById("createOrderForm");
  const formData = new FormData(form);
  const data = Object.fromEntries(formData.entries());
  const rawText = document.getElementById("rawOrderText").value.trim();
  const parsedFromRaw = rawText ? parseRawTextByRules(rawText) : null;
  const jobData = parsedFromRaw || buildJobDataFromForm(data);
  jobData.orderCode = data.code || jobData.orderCode || "Chưa có";
  jobData.jobTitle = data.title || jobData.jobTitle || "Đơn tuyển";
  jobData.industries = splitIndustries(data.department || jobData.industries || jobData.industry);
  jobData.industry = jobData.industries[0] || data.department || jobData.industry || "Đơn tuyển";
  jobData.location = data.location || jobData.location || "Chưa rõ";
  jobData.quantity = Number(data.headcount || 0) || jobData.quantity || "";
  jobData.source = {
    ...(jobData.source || {}),
    image_ocr_used: imageDataReady,
    pasted_text_used: Boolean(rawText),
  };
  const imageElement = document.getElementById("imagePasteBox").querySelector("img");
  const textUpFb = makeShortText(jobData);
  const jobJson = JSON.stringify(buildJobJson(jobData), null, 2);
  return {
    ...data,
    industry: jobData.industry,
    industries: jobData.industries,
    department: jobData.industry,
    headcount: Number(data.headcount || 0),
    rawText,
    hasImage: imageElement !== null,
    hasImageData: imageDataReady,
    textUpFb,
    jobJson,
    imageDataUrl: imageElement ? imageElement.src : "",
  };
}

function findTextValue(rawText, labels) {
  const lines = rawText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (const label of labels) {
    const matcher = new RegExp(`^${label}\\s*[:\\-]\\s*(.+)$`, "i");
    const found = lines.find((line) => matcher.test(line));
    if (found) return found.replace(matcher, "$1").trim();
  }
  return "";
}

function buildJobDataFromForm(data) {
  return {
    orderCode: data.code || "Chưa có",
    orderType: "Đơn tuyển",
    industry: splitIndustries(data.department)[0] || "Đơn tuyển",
    industries: splitIndustries(data.department),
    jobTitle: data.title || "Đơn tuyển",
    location: data.location || "Chưa rõ",
    company: "",
    salaryText: "Liên hệ",
    allowance: "",
    housing: "",
    insurance: "",
    workingHours: "",
    benefit: "",
    requirement: "Liên hệ",
    japaneseLevel: "",
    salaryNumber: "",
    salaryPeriod: "",
    interview: "Liên hệ",
    quantity: Number(data.headcount || 0) || "",
    daysOff: "Liên hệ",
    work: data.title || "Đơn tuyển",
    back: "",
    source: {
      image_ocr_used: imageDataReady,
      pasted_text_used: Boolean(document.getElementById("rawOrderText")?.value.trim()),
    },
  };
}

function parseOrderText() {
  const rawText = document.getElementById("rawOrderText").value.trim();
  const jobData = parseRawTextByRules(rawText);
  imageDataReady = document.getElementById("imagePasteBox").querySelector("img") !== null;
  updateCreateImageBadges();
  setCreateOrderFields({
    code: jobData.orderCode !== "Chưa có" ? jobData.orderCode : "",
    title: jobData.jobTitle,
    department: (jobData.industries && jobData.industries.length ? jobData.industries : [jobData.industry]).filter(Boolean).join(", "),
    location: jobData.location !== "Chưa rõ" ? jobData.location : "",
    headcount: jobData.quantity || 1,
  });
  updateCreatePreview(jobData);
}

function setCreateOrderFields(data) {
  const form = document.getElementById("createOrderForm");
  if (data.code) form.elements.code.value = data.code;
  if (data.title) form.elements.title.value = data.title;
  if (data.department) form.elements.department.value = data.department;
  if (data.location) form.elements.location.value = data.location;
  if (data.headcount) form.elements.headcount.value = data.headcount;
  updateCreatePreview();
}

function updateCreatePreview(parsedJobData) {
  const data = getCreateOrderFormData();
  const jobData = parsedJobData || {
    orderCode: data.code || "Chưa có",
    orderType: "Đơn tuyển",
    industry: splitIndustries(data.department)[0] || "Đơn tuyển",
    industries: splitIndustries(data.department),
    jobTitle: data.title || "Đơn tuyển",
    location: data.location || "Chưa rõ",
    company: "",
    salaryText: "Liên hệ",
    allowance: "",
    housing: "",
    insurance: "",
    workingHours: "",
    benefit: "",
    requirement: "Liên hệ",
    japaneseLevel: "",
    salaryNumber: "",
    salaryPeriod: "",
    interview: "Liên hệ",
    quantity: data.headcount || "",
    daysOff: "Liên hệ",
    work: data.title || "Đơn tuyển",
    back: "",
    source: {
      image_ocr_used: false,
      pasted_text_used: Boolean(data.rawText),
    },
  };
  const shortText = makeShortText(jobData);

  document.getElementById("previewOrderCode").textContent = `Mã đơn: ${data.code || "--"}`;
  document.getElementById("facebookPreviewText").innerHTML = escapeHtml(shortText || "Nhập hoặc tách dữ liệu để xem preview").replace(/\n/g, "<br>");
  document.getElementById("createOrderJson").textContent = JSON.stringify(buildJobJson(jobData), null, 2);
}

function updateCreateImageBadges() {
  const hasImage = document.getElementById("imagePasteBox").querySelector("img") !== null;
  const imageStatus = document.getElementById("createImageStatus");
  const imageDataStatus = document.getElementById("createImageDataStatus");

  imageStatus.textContent = hasImage ? "Có ảnh" : "Không ảnh";
  imageStatus.classList.toggle("ok", hasImage);
  imageStatus.classList.toggle("warn", !hasImage);

  imageDataStatus.textContent = imageDataReady ? "Có data ảnh" : "Chưa có data ảnh";
  imageDataStatus.classList.toggle("ok", imageDataReady);
  imageDataStatus.classList.toggle("bad", !imageDataReady);
}

function renderMetrics(metrics) {
  currentMetrics = { ...metrics };
  document.getElementById("openOrdersMetric").textContent = metrics.openOrders;
  document.getElementById("newCandidatesMetric").textContent = metrics.newCandidates;
  document.getElementById("interviewingMetric").textContent = metrics.interviewing;
  document.getElementById("activeCollaboratorsMetric").textContent = metrics.activeCollaborators;
}

function updateActiveCollaboratorsMetric(count) {
  if (currentMetrics) currentMetrics.activeCollaborators = count;
  document.getElementById("activeCollaboratorsMetric").textContent = count;
}

function getVisibleOrders(orders) {
  const search = document.getElementById("orderSearch")?.value.trim().toLowerCase() || "";
  const sortBy = document.getElementById("orderSortBy")?.value || "createdAt";
  const direction = document.getElementById("orderSortDirection")?.value || "desc";

  return [...orders]
    .filter((order) => {
      if (!search) return true;
      return [order.code, order.title, order.department, getOrderLocation(order), order.status, order.postingStatus]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(search));
    })
    .sort((a, b) => {
      let first = "";
      let second = "";

      if (sortBy === "industry") {
        first = a.department || "";
        second = b.department || "";
      } else if (sortBy === "name") {
        first = `${a.code || ""} ${a.title || ""}`;
        second = `${b.code || ""} ${b.title || ""}`;
      } else {
        first = a.createdAt || "";
        second = b.createdAt || "";
      }

      const result = String(first).localeCompare(String(second), "vi", { numeric: true, sensitivity: "base" });
      return direction === "asc" ? result : -result;
    });
}

function updateOrderSearchOptions(orders) {
  const datalist = document.getElementById("orderSearchOptions");
  if (!datalist) return;

  const options = new Set();
  orders.forEach((order) => {
    [order.code, order.title, order.department, getOrderLocation(order)].filter(Boolean).forEach((value) => options.add(value));
    getOrderIndustries(order).forEach((industry) => options.add(industry));
  });

  datalist.innerHTML = [...options]
    .slice(0, 80)
    .map((value) => `<option value="${escapeHtml(value)}"></option>`)
    .join("");
}

function normalizeSearchText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .trim();
}

function getOrderIndustries(order) {
  if (Array.isArray(order.industries) && order.industries.length) {
    return splitIndustries(order.industries);
  }
  const savedIndustries = readJsonValue(order.jobJson, "industries");
  if (Array.isArray(savedIndustries) && savedIndustries.length) {
    return splitIndustries(savedIndustries);
  }
  const rawIndustry = order.department || readJsonValue(order.jobJson, "industry") || "";
  const title = order.title || readJsonValue(order.jobJson, "job_title") || "";
  const orderCode = order.code || readJsonValue(order.jobJson, "order_code") || "";
  const combined = normalizeSearchText(`${orderCode} ${rawIndustry} ${title}`);
  const industries = [];
  const addIndustry = (industry) => {
    if (!industries.includes(industry)) industries.push(industry);
  };

  if (/khach san|ve sinh|vstn|don dep/.test(combined)) addIndustry("Khách sạn");
  if (/\bpc1\b/.test(combined) && /han|han xi/.test(combined)) addIndustry("Xây dựng");
  if (/agt|xay dung|cot thep|gian giao|be tong|cong truong|cong trinh|giai the|ky su quan ly cong trinh|ks qly cong trinh/.test(combined)) addIndustry("Xây dựng");
  if (/han|co khi|san xuat|factory|may/.test(combined)) addIndustry("Cơ khí/Sản xuất");
  if (/thuc pham|che bien|food|nong nghiep/.test(combined)) addIndustry("Thực phẩm");
  if (!industries.length) addIndustry(cleanIndustryLabel(rawIndustry || "Chưa có ngành"));

  return industries;
}

function getIndustryTagClass(industry) {
  const value = normalizeSearchText(industry);
  if (/cot thep|thi cong cot thep/.test(value)) return "steel";
  if (/agt|xay dung|gian giao|be tong|cong truong|cong trinh|giai the/.test(value)) return "construction";
  if (/khach san|ve sinh|vstn|don dep/.test(value)) return "hotel";
  if (/han|co khi|san xuat|factory|may/.test(value)) return "factory";
  if (/thuc pham|che bien|food|nong nghiep/.test(value)) return "food";
  return "default";
}

function getOrderShortTitle(order) {
  const fullTitle = order.title || readJsonValue(order.jobJson, "job_title") || "";
  const shortTitle = shortenFacebookWords(cleanJobTitle(fullTitle))
    .replace(/\s+/g, " ")
    .trim();
  return shortTitle || fullTitle || "Chưa có tên đơn";
}

function getOrderLocation(order) {
  return order.location || readJsonValue(order.jobJson, "location") || "Chưa rõ";
}

function getOrderSearchLabel(order) {
  return `${order.code} - ${getOrderShortTitle(order)}`;
}

function getOrderIndustryLabel(order) {
  const industries = getOrderIndustries(order || {}).filter(Boolean);
  return industries.length ? industries.join(", ") : "Chưa có ngành";
}

function populateCandidateOrderOptions() {
  const datalist = document.getElementById("candidateOrderOptions");
  if (!datalist) return;

  datalist.innerHTML = currentOrders
    .map((order) => {
      const label = getOrderSearchLabel(order);
      return `<option value="${escapeHtml(label)}" data-code="${escapeHtml(order.code)}">${escapeHtml(getOrderIndustryLabel(order))}</option>`;
    })
    .join("");
}

function findOrderFromCandidateInput() {
  const value = document.getElementById("candidateOrderSearch")?.value.trim() || "";
  return currentOrders.find((order) => value === getOrderSearchLabel(order) || value === order.code) || null;
}

function syncCandidateOrderFields() {
  const order = findOrderFromCandidateInput();
  document.getElementById("candidateOrderId").value = order?.id || order?.code || "";
  document.getElementById("candidateRole").value = order ? getOrderIndustryLabel(order) : "";
  document.getElementById("candidateIndustryPreview").value = order ? getOrderIndustryLabel(order) : "";
  return order;
}

function getCtvSearchLabel(ctv) {
  const name = ctv.fullName || ctv.name || "Chưa có tên";
  return `${formatShortId(ctv.id)} - ${name}`;
}

function populateCandidateCtvOptions() {
  const datalist = document.getElementById("candidateCtvOptions");
  if (!datalist) return;

  datalist.innerHTML = currentCtvs
    .map((ctv) => {
      const label = getCtvSearchLabel(ctv);
      return `<option value="${escapeHtml(label)}" data-id="${escapeHtml(ctv.id || "")}"></option>`;
    })
    .join("");
}

function findCtvFromCandidateInput() {
  const value = document.getElementById("candidateCtvSearch")?.value.trim() || "";
  return currentCtvs.find((ctv) => value === getCtvSearchLabel(ctv) || value === ctv.id || value === formatShortId(ctv.id)) || null;
}

function syncCandidateCtvFields() {
  const ctv = findCtvFromCandidateInput();
  document.getElementById("candidateCtvId").value = ctv?.id || "";
  return ctv;
}

async function prepareCandidateModalOptions() {
  if (!currentOrders.length) {
    const data = await loadDashboard();
    currentOrders = data.orders || [];
  }
  if (!ctvsLoaded) {
    currentCtvs = await loadCtvs();
    ctvsLoaded = true;
  }
  populateCandidateOrderOptions();
  populateCandidateCtvOptions();
}

function buildCandidateEditSnapshot(payload, selectedOrder, selectedCtv) {
  return normalizeComparableRecord({
    fullName: payload.fullName,
    phone: payload.phone,
    email: payload.email,
    groupLink: payload.groupLink,
    stage: payload.stage,
    orderId: selectedOrder?.id || "",
    role: selectedOrder ? getOrderIndustryLabel(selectedOrder) : "",
    ctvId: selectedCtv?.id || "",
    sourceNote: selectedCtv ? (selectedCtv.fullName || selectedCtv.name || "") : payload.source || "",
  });
}

function getCtvRecruitedCount(ctv) {
  return Number(ctv.recruitedCount ?? ctv.hiredCount ?? ctv.passCount ?? ctv.totalHired ?? ctv.candidateCount ?? 0) || 0;
}

function getCtvZaloLink(ctv) {
  if (ctv.zaloLink || ctv.zalo) return ctv.zaloLink || ctv.zalo;
  const digits = String(ctv.phone || "").replace(/\D/g, "");
  return digits ? `https://zalo.me/${digits}` : "";
}

function renderOrders(orders) {
  const list = document.getElementById("orderList") || document.getElementById("ordersList");
  const visibleOrders = getVisibleOrders(orders);

  updateOrderSearchOptions(orders);

  if (visibleOrders.length === 0) {
    list.innerHTML = `
      <article class="order-card order-card-empty">
        <div class="empty-state">
          <strong>Chưa có đơn phù hợp</strong>
          <span>Thử đổi từ khóa tìm kiếm hoặc kiểm tra lại database.</span>
        </div>
      </article>
    `;
    return;
  }

  list.innerHTML = visibleOrders
    .map(
      (order) => {
        const shortTitle = getOrderShortTitle(order);
        const fullTitle = order.title || readJsonValue(order.jobJson, "job_title") || "";

        return `
        <article class="order-card">
          <div class="order-compact">
            <div class="compact-cell order-main">
              <strong title="${escapeHtml(fullTitle)}">${escapeHtml(order.code)} - ${escapeHtml(shortTitle)}</strong>
            </div>

            <div class="order-actions">
              <button class="dark-action" type="button" data-reopen-code="${escapeHtml(order.code)}">Mở lại</button>
              <button class="copy-text-action" type="button" data-copy-order-code="${escapeHtml(order.code)}">Copy text</button>
              <button class="red-action" type="button" data-delete-code="${escapeHtml(order.code)}">Xóa</button>
            </div>

            <div class="order-info">
              <span class="order-industry-tags">
                ${getOrderIndustries(order)
                  .map((industry) => `<span class="industry-tag ${getIndustryTagClass(industry)}">${escapeHtml(industry)}</span>`)
                  .join("")}
              </span>
              <span class="meta-line"><b>Tỉnh</b><span>${escapeHtml(getOrderLocation(order))}</span></span>
              <span class="meta-line"><b>Tạo</b><span>${escapeHtml(formatShortDateTime(order.createdAt))}</span></span>
              <span class="badges" aria-label="Trạng thái dữ liệu">
                <span class="badge ${order.hasImage ? "ok" : "warn"}">${order.hasImage ? "Có ảnh" : "Không ảnh"}</span>
                <span class="badge ${order.hasImageData ? "ok" : "bad"}">${order.hasImageData ? "Có data ảnh" : "Chưa có data ảnh"}</span>
              </span>
            </div>
          </div>

          <div class="order-more">
            <button class="detail-toggle" type="button" data-order-code="${escapeHtml(order.code)}">Chi tiết hình ảnh, text và JSON</button>
          </div>

        </article>
      `;
      },
    )
    .join("");

  list.querySelectorAll("[data-order-code]").forEach((button) => {
    button.addEventListener("click", async () => {
      const order = currentOrders.find((item) => item.code === button.dataset.orderCode);
      if (!order) return;
      button.disabled = true;
      try {
        openOrderDetail(await ensureOrderDetail(order));
      } catch (error) {
        console.error(error);
      } finally {
        button.disabled = false;
      }
    });
  });
  list.querySelectorAll("[data-reopen-code]").forEach((button) => {
    button.addEventListener("click", async () => {
      const order = currentOrders.find((item) => item.code === button.dataset.reopenCode);
      if (!order) return;
      const persisted = getPersistedOrderDetail(order.code, order.updatedAt);
      if (persisted) {
        reopenOrder({ ...order, ...persisted });
        return;
      }
      reopenOrder(order, { loadingDetail: true });
      button.disabled = true;
      try {
        const detail = await ensureOrderDetail(order);
        const dialog = document.getElementById("createOrderDialog");
        if (dialog.open && editingOrderCode === detail.code) {
          reopenOrder(detail);
        }
      } catch (error) {
        console.error(error);
        document.getElementById("createOrderMessage").textContent = error.message || "Không thể tải dữ liệu đã lưu.";
        document.getElementById("createOrderMessage").className = "form-message error";
        document.getElementById("createOrderForm").querySelector('button[type="submit"]').disabled = false;
      } finally {
        button.disabled = false;
      }
    });
  });
  list.querySelectorAll("[data-copy-order-code]").forEach((button) => {
    button.addEventListener("click", async () => {
      const order = currentOrders.find((item) => item.code === button.dataset.copyOrderCode);
      if (!order) return;
      button.disabled = true;
      const originalLabel = button.textContent;
      try {
        const detail = await ensureOrderDetail(order);
        const fallbackText = makeShortText(buildJobDataFromOrder(detail));
        const text = (detail.textUpFb || fallbackText || "").trim();
        if (!text) throw new Error("Đơn này chưa có text up Facebook.");
        await navigator.clipboard.writeText(text);
        button.textContent = "Đã copy";
        button.classList.add("copied");
        setTimeout(() => {
          button.textContent = originalLabel;
          button.classList.remove("copied");
        }, 1200);
      } catch (error) {
        console.error(error);
        button.textContent = "Lỗi copy";
        setTimeout(() => {
          button.textContent = originalLabel;
        }, 1200);
      } finally {
        button.disabled = false;
      }
    });
  });
  list.querySelectorAll("[data-delete-code]").forEach((button) => {
    button.addEventListener("click", () => {
      const order = currentOrders.find((item) => item.code === button.dataset.deleteCode);
      openDeleteDialog(order || { code: button.dataset.deleteCode, title: "" });
    });
  });
  if (window.lucide) window.lucide.createIcons();
}

function getCandidateApplication(candidate) {
  return currentApplications.find((application) => application.candidateId === candidate.id) || null;
}

function getCandidateOrderLabel(candidate, application) {
  if (!application) return candidate.orderName || candidate.orderTitle || "Chưa có";
  return [application.orderCode, application.orderTitle].filter(Boolean).join(" - ") || "Chưa có";
}

function getCandidateOrderCode(candidate, application) {
  return application?.orderCode || candidate.orderCode || candidate.orderName || candidate.orderTitle || "";
}

function getCandidateOrder(candidate, application) {
  const orderCode = getCandidateOrderCode(candidate, application);
  if (!orderCode) return null;
  return currentOrders.find((order) => order.code === orderCode) || null;
}

function renderCandidateOrderButton(candidate, application) {
  const orderCode = getCandidateOrderCode(candidate, application);
  if (!orderCode) return `<span>Chưa có</span>`;
  const order = getCandidateOrder(candidate, application);
  if (!order) return `<span title="${escapeHtml(getCandidateOrderLabel(candidate, application))}">${escapeHtml(orderCode)}</span>`;
  return `<button class="table-link table-link-button order-participation-link" type="button" data-candidate-order-code="${escapeHtml(order.code)}" title="${escapeHtml(getCandidateOrderLabel(candidate, application))}">${escapeHtml(order.code)}</button>`;
}

function getCandidateIndustry(candidate, application) {
  const order = currentOrders.find((item) => item.code === application?.orderCode);
  return order?.department || candidate.industry || application?.role || candidate.role || "Chưa có";
}

function renderCandidateIndustryTags(candidate, application) {
  const order = getCandidateOrder(candidate, application);
  const industries = order ? getOrderIndustries(order) : [getCandidateIndustry(candidate, application)];
  return `<span class="candidate-industry-tags">${industries
    .map((industry) => `<span class="industry-tag ${getIndustryTagClass(industry)}">${escapeHtml(industry)}</span>`)
    .join("")}</span>`;
}

function getCandidateStatus(candidate, application) {
  const value = normalizeSearchText(application?.stage || application?.status || candidate.stage || candidate.status || "");
  if (/hoan thanh|nhan viec|offer|dat|pass|ve cty xong/.test(value)) return "Hoàn thành";
  if (/cho ve cty|ve cty|dang ve|len cty/.test(value)) return "Chờ về cty";
  return "Chờ PV";
}

function getCandidateStatusClass(status) {
  const value = normalizeSearchText(status);
  if (/hoan thanh/.test(value)) return "done";
  if (/cho ve cty/.test(value)) return "returning";
  return "interview";
}

function renderCandidateStatusControl(candidate, application) {
  const status = getCandidateStatus(candidate, application);
  const appId = application?.id || "";
  const scheduleButton = status === "Chờ PV"
    ? `<button class="interview-mini-button" type="button" data-interview-application-id="${escapeHtml(appId)}" data-interview-candidate-id="${escapeHtml(candidate.id || "")}" aria-label="Xem lịch PV"><i data-lucide="calendar-days" aria-hidden="true"></i></button>`
    : "";
  return `<span class="candidate-status-wrap"><span class="candidate-status ${getCandidateStatusClass(status)}">${escapeHtml(status)}</span>${scheduleButton}</span>`;
}

function getCandidateCtv(candidate, application) {
  return application?.ctvName || candidate.ctvName || candidate.collaboratorName || candidate.source || "Chưa có";
}

function getCandidateCtvLink(candidate, application) {
  const ctvName = getCandidateCtv(candidate, application);
  const ctv = currentCtvs.find((item) => {
    if (application?.ctvId && item.id === application.ctvId) return true;
    return normalizeSearchText(item.fullName || item.name || "") === normalizeSearchText(ctvName);
  });
  if (ctv) return getCtvZaloLink(ctv);
  return application?.ctvZaloLink || candidate.ctvZaloLink || "";
}

function getCandidateJoinedAt(candidate, application) {
  return application?.appliedAt || application?.createdAt || candidate.appliedAt || candidate.createdAt || "";
}

function formatShortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
  }).formatToParts(date);
  const getPart = (type) => parts.find((part) => part.type === type)?.value || "";
  return `${getPart("day")}/${getPart("month")}/${getPart("year")}`;
}

function formatShortDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const getPart = (type) => parts.find((part) => part.type === type)?.value || "";
  return `${getPart("day")}/${getPart("month")}/${getPart("year")} ${getPart("hour")}:${getPart("minute")}`;
}

function getCandidateLink(candidate, keys) {
  const value = keys.map((key) => candidate[key]).find(Boolean);
  if (!value) return "";
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

function getCandidateGroupLink(candidate, application) {
  const value = application?.groupLink || application?.groupUrl || application?.facebookGroup || application?.sourceLink || getCandidateLink(candidate, ["groupLink", "groupUrl", "facebookGroup", "sourceLink"]);
  if (!value) return "";
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

function getCandidateZaloLink(candidate) {
  const directLink = getCandidateLink(candidate, ["zaloLink", "zaloUrl", "zalo", "zaloGroup"]);
  if (directLink) return directLink;
  const noteLink = String(candidate.note || "").match(/https?:\/\/(?:zalo\.me|zaloapp\.com|chat\.zalo\.me)[^\s]+/i);
  if (noteLink) return noteLink[0];
  const digits = String(candidate.phone || "").replace(/\D/g, "");
  return digits ? `https://zalo.me/${digits}` : "";
}

function renderLinkedName(name, link) {
  const label = escapeHtml(name || "Chưa có tên");
  if (!link) return `<strong>${label}</strong>`;
  return `<strong><a class="table-link name-link" href="${escapeHtml(link)}" target="_blank" rel="noopener">${label}</a></strong>`;
}

function renderCandidateNameCell(candidate, zaloLink) {
  const phone = candidate.phone || "Chưa có SĐT";
  return `
    <span class="candidate-name-cell">
      ${renderLinkedName(candidate.fullName || candidate.name, zaloLink)}
      <small>${escapeHtml(phone)}</small>
    </span>
  `;
}

function renderCandidateOrderCell(candidate, application) {
  return `
    <span class="candidate-order-cell">
      ${renderCandidateOrderButton(candidate, application)}
    </span>
  `;
}

function renderCandidateIndustryCell(candidate, application) {
  return `
    <span class="candidate-industry-cell">
      ${renderCandidateIndustryTags(candidate, application)}
    </span>
  `;
}

function renderCandidateGroupCell(groupLink) {
  return `
    <span class="candidate-group-cell">
      ${groupLink ? `<a class="table-link" href="${escapeHtml(groupLink)}" target="_blank" rel="noopener">Mở nhóm</a>` : `<span class="muted-value">Chưa có</span>`}
    </span>
  `;
}

function renderCandidateTable(candidates) {
  const list = document.getElementById("orderList") || document.getElementById("ordersList");
  const search = document.getElementById("candidateSearch")?.value.trim() || "";
  const stage = document.getElementById("candidateStageFilter")?.value || "all";
  const sortBy = document.getElementById("candidateSortBy")?.value || "joinedAt";
  const direction = document.getElementById("candidateSortDirection")?.value || "desc";
  const query = normalizeSearchText(search);
  const visibleCandidates = [...candidates]
    .filter((candidate) => {
      const application = getCandidateApplication(candidate);
      const matchesSearch = !query || normalizeSearchText([
        candidate.fullName || candidate.name,
        getCandidateOrderLabel(candidate, application),
        getCandidateIndustry(candidate, application),
        getCandidateStatus(candidate, application),
        getCandidateCtv(candidate, application),
        getCandidateJoinedAt(candidate, application),
        candidate.zaloLink,
        application?.groupLink || candidate.groupLink,
      ].filter(Boolean).join(" ")).includes(query);
      const matchesStage = stage === "all" || getCandidateStatus(candidate, application) === stage;
      return matchesSearch && matchesStage;
    })
    .sort((a, b) => {
      const multiplier = direction === "asc" ? 1 : -1;
      const firstApp = getCandidateApplication(a);
      const secondApp = getCandidateApplication(b);
      const first = sortBy === "name" ? (a.fullName || a.name || "") : sortBy === "stage" ? getCandidateStatus(a, firstApp) : getCandidateJoinedAt(a, firstApp);
      const second = sortBy === "name" ? (b.fullName || b.name || "") : sortBy === "stage" ? getCandidateStatus(b, secondApp) : getCandidateJoinedAt(b, secondApp);
      return String(first).localeCompare(String(second), "vi", { numeric: true, sensitivity: "base" }) * multiplier;
    });

  const stages = ["Chờ PV", "Chờ về cty", "Hoàn thành"];

  list.innerHTML = `
    <div class="clean-toolbar">
      <label class="clean-search">Tìm ứng viên
        <input id="candidateSearch" type="search" value="${escapeHtml(search)}" placeholder="Tên, đơn, ngành, trạng thái...">
      </label>
      <label>Trạng thái
        <select id="candidateStageFilter">
          <option value="all">Tất cả</option>
          ${stages.map((item) => `<option value="${escapeHtml(item)}" ${stage === item ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}
        </select>
      </label>
      <label>Sắp xếp
        <select id="candidateSortBy">
          <option value="joinedAt" ${sortBy === "joinedAt" ? "selected" : ""}>Thời gian tham gia</option>
          <option value="name" ${sortBy === "name" ? "selected" : ""}>Tên</option>
          <option value="stage" ${sortBy === "stage" ? "selected" : ""}>Trạng thái</option>
        </select>
      </label>
      <label>Chiều
        <select id="candidateSortDirection">
          <option value="desc" ${direction === "desc" ? "selected" : ""}>Mới/Z-A</option>
          <option value="asc" ${direction === "asc" ? "selected" : ""}>Cũ/A-Z</option>
        </select>
      </label>
    </div>
    <div class="clean-table candidate-table">
      <div class="clean-table-head">
        <span></span>
        <span>Ứng viên</span>
        <span>Mã đơn</span>
        <span>Ngành</span>
        <span>Trạng thái</span>
        <span>CTV</span>
        <span>Link nhóm</span>
        <span>Ngày</span>
        <span>Xóa</span>
      </div>
      ${visibleCandidates.length === 0 ? `
        <div class="clean-table-empty">
          <strong>${candidates.length === 0 ? "Chưa có ứng viên" : "Không tìm thấy ứng viên"}</strong>
          <span>${candidates.length === 0 ? "Dữ liệu ở đây lấy trực tiếp từ database, không dùng dữ liệu mẫu." : "Thử đổi từ khóa hoặc bộ lọc trạng thái."}</span>
        </div>
      ` : visibleCandidates
        .map((candidate) => {
          const application = getCandidateApplication(candidate);
          const zaloLink = getCandidateZaloLink(candidate);
          const ctvName = getCandidateCtv(candidate, application);
          const ctvLink = getCandidateCtvLink(candidate, application);
          const groupLink = getCandidateGroupLink(candidate, application);
          return `
            <div class="clean-table-row">
              <span class="candidate-action-cell">
                <button class="row-icon-button" type="button" data-add-candidate-from-row="${escapeHtml(candidate.id || "")}" aria-label="Chỉnh sửa ứng viên">
                  <i data-lucide="pencil" aria-hidden="true"></i>
                </button>
              </span>
              ${renderCandidateNameCell(candidate, zaloLink)}
              ${renderCandidateOrderCell(candidate, application)}
              ${renderCandidateIndustryCell(candidate, application)}
              <span class="candidate-status-cell">${renderCandidateStatusControl(candidate, application)}</span>
              <span class="candidate-ctv-cell">
                ${ctvLink ? `<a class="table-link name-link" href="${escapeHtml(ctvLink)}" target="_blank" rel="noopener">${escapeHtml(ctvName)}</a>` : escapeHtml(ctvName)}
              </span>
              ${renderCandidateGroupCell(groupLink)}
              <span class="candidate-date-cell">${escapeHtml(formatShortDate(getCandidateJoinedAt(candidate, application)))}</span>
              <span class="candidate-delete-cell">
                <button class="row-delete-button" type="button" data-delete-candidate-id="${escapeHtml(candidate.id || "")}" data-delete-candidate-name="${escapeHtml(candidate.fullName || candidate.name || "ứng viên")}">Xóa</button>
              </span>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
  list.querySelectorAll("[data-candidate-order-code]").forEach((button) => {
    button.addEventListener("click", async () => {
      const order = currentOrders.find((item) => item.code === button.dataset.candidateOrderCode);
      if (!order) return;
      button.disabled = true;
      try {
        openOrderDetail(await ensureOrderDetail(order));
      } catch (error) {
        console.error(error);
      } finally {
        button.disabled = false;
      }
    });
  });
  list.querySelectorAll("[data-add-candidate-from-row]").forEach((button) => {
    button.addEventListener("click", () => {
      openEditCandidateModal(button.dataset.addCandidateFromRow).catch((error) => {
        console.error(error);
        alert(error.message || "Không thể mở form chỉnh sửa ứng viên");
      });
    });
  });
  list.querySelectorAll("[data-delete-candidate-id]").forEach((button) => {
    button.addEventListener("click", () => {
      openDeleteCandidateDialog(button.dataset.deleteCandidateId, button.dataset.deleteCandidateName || "ứng viên");
    });
  });
  list.querySelectorAll("[data-interview-application-id], [data-interview-candidate-id]").forEach((button) => {
    button.addEventListener("click", () => {
      openInterviewScheduleDialog(button.dataset.interviewApplicationId, button.dataset.interviewCandidateId);
    });
  });
  if (window.lucide) window.lucide.createIcons();
  bindPeopleToolbars();
}

function renderCtvTable(ctvs) {
  const list = document.getElementById("orderList") || document.getElementById("ordersList");
  updateActiveCollaboratorsMetric(ctvs.length);
  const search = document.getElementById("ctvSearch")?.value.trim() || "";
  const sortBy = document.getElementById("ctvSortBy")?.value || "createdAt";
  const direction = document.getElementById("ctvSortDirection")?.value || "desc";
  const query = normalizeSearchText(search);
  const visibleCtvs = [...ctvs]
    .filter((ctv) => {
      if (!query) return true;
      return normalizeSearchText([
        ctv.id,
        formatShortId(ctv.id),
        ctv.fullName || ctv.name,
        ctv.createdAt,
        getCtvRecruitedCount(ctv),
        getCtvZaloLink(ctv),
        ctv.phone,
      ].filter(Boolean).join(" ")).includes(query);
    })
    .sort((a, b) => {
      const multiplier = direction === "asc" ? 1 : -1;
      const first = sortBy === "name" ? (a.fullName || a.name || "") : sortBy === "recruited" ? getCtvRecruitedCount(a) : sortBy === "id" ? formatShortId(a.id) : (a.createdAt || "");
      const second = sortBy === "name" ? (b.fullName || b.name || "") : sortBy === "recruited" ? getCtvRecruitedCount(b) : sortBy === "id" ? formatShortId(b.id) : (b.createdAt || "");
      return String(first).localeCompare(String(second), "vi", { numeric: true, sensitivity: "base" }) * multiplier;
    });

  list.innerHTML = `
    <div class="clean-toolbar">
      <label class="clean-search">Tìm CTV
        <input id="ctvSearch" type="search" value="${escapeHtml(search)}" placeholder="ID, họ tên, Zalo...">
      </label>
      <label>Sắp xếp
        <select id="ctvSortBy">
          <option value="createdAt" ${sortBy === "createdAt" ? "selected" : ""}>Ngày tham gia</option>
          <option value="id" ${sortBy === "id" ? "selected" : ""}>ID</option>
          <option value="name" ${sortBy === "name" ? "selected" : ""}>Tên</option>
          <option value="recruited" ${sortBy === "recruited" ? "selected" : ""}>Số UV đã tuyển</option>
        </select>
      </label>
      <label>Chiều
        <select id="ctvSortDirection">
          <option value="desc" ${direction === "desc" ? "selected" : ""}>Mới/Z-A</option>
          <option value="asc" ${direction === "asc" ? "selected" : ""}>Cũ/A-Z</option>
        </select>
      </label>
    </div>
    <div class="clean-table ctv-table">
      <div class="clean-table-head">
        <span>ID</span>
        <span>Họ tên</span>
        <span>Ngày tham gia</span>
        <span>Số UV đã tuyển</span>
        <span>Chi tiết</span>
        <span>Xóa</span>
      </div>
      ${visibleCtvs.length === 0 ? `
        <div class="clean-table-empty">
          <strong>${ctvs.length === 0 ? "Chưa có cộng tác viên" : "Không tìm thấy CTV"}</strong>
          <span>${ctvs.length === 0 ? "Dữ liệu ở đây lấy trực tiếp từ database, không dùng dữ liệu mẫu." : "Thử đổi từ khóa hoặc cách sắp xếp."}</span>
        </div>
      ` : visibleCtvs
        .map(
          (ctv) => {
            const zaloLink = getCtvZaloLink(ctv);
            return `
            <div class="clean-table-row">
              <span title="${escapeHtml(ctv.id || "")}">${escapeHtml(formatShortId(ctv.id))}</span>
              ${renderLinkedName(ctv.fullName || ctv.name, zaloLink)}
              <span>${escapeHtml(formatShortDate(ctv.createdAt))}</span>
              <span><span class="badge">${getCtvRecruitedCount(ctv)}</span></span>
              <span><button class="table-link table-link-button" type="button" data-ctv-detail-id="${escapeHtml(ctv.id || "")}">Chi tiết</button></span>
              <span><button class="row-delete-button" type="button" data-delete-ctv-id="${escapeHtml(ctv.id || "")}" data-delete-ctv-name="${escapeHtml(ctv.fullName || ctv.name || "CTV")}">Xóa</button></span>
            </div>
          `;
          },
        )
        .join("")}
    </div>
  `;
  list.querySelectorAll("[data-ctv-detail-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const ctv = currentCtvs.find((item) => item.id === button.dataset.ctvDetailId);
      if (ctv) openCtvDetail(ctv);
    });
  });
  list.querySelectorAll("[data-delete-ctv-id]").forEach((button) => {
    button.addEventListener("click", () => {
      openDeleteCtvDialog(button.dataset.deleteCtvId, button.dataset.deleteCtvName || "CTV");
    });
  });
  bindPeopleToolbars();
}

function bindPeopleToolbars() {
  const keepFocus = (id, render) => {
    const field = document.getElementById(id);
    const cursor = field && "selectionStart" in field ? field.selectionStart : null;
    render();
    const nextField = document.getElementById(id);
    if (!nextField) return;
    nextField.focus();
    if (cursor !== null && "setSelectionRange" in nextField) {
      nextField.setSelectionRange(cursor, cursor);
    }
  };

  ["candidateSearch", "candidateStageFilter", "candidateSortBy", "candidateSortDirection"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => keepFocus(id, () => renderCandidateTable(currentCandidates)));
    document.getElementById(id)?.addEventListener("change", () => keepFocus(id, () => renderCandidateTable(currentCandidates)));
  });
  ["ctvSearch", "ctvSortBy", "ctvSortDirection"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => keepFocus(id, () => renderCtvTable(currentCtvs)));
    document.getElementById(id)?.addEventListener("change", () => keepFocus(id, () => renderCtvTable(currentCtvs)));
  });
}

function renderCandidateLoadingTable() {
  const list = document.getElementById("orderList") || document.getElementById("ordersList");
  list.innerHTML = `
    <div class="clean-toolbar">
      <label class="clean-search">Tìm ứng viên
        <input id="candidateSearch" type="search" placeholder="Tên, vị trí, nguồn...">
      </label>
      <label>Trạng thái
        <select id="candidateStageFilter">
          <option value="all">Tất cả</option>
          <option value="Chờ PV">Chờ PV</option>
          <option value="Chờ về cty">Chờ về cty</option>
          <option value="Hoàn thành">Hoàn thành</option>
        </select>
      </label>
      <label>Sắp xếp
        <select id="candidateSortBy">
          <option value="joinedAt">Thời gian tham gia</option>
          <option value="name">Tên</option>
          <option value="stage">Trạng thái</option>
        </select>
      </label>
      <label>Chiều
        <select id="candidateSortDirection">
          <option value="desc">Mới/Z-A</option>
          <option value="asc">Cũ/A-Z</option>
        </select>
      </label>
    </div>
    <div class="clean-table candidate-table">
      <div class="clean-table-head">
        <span></span>
        <span>Ứng viên</span>
        <span>Mã đơn</span>
        <span>Ngành</span>
        <span>Trạng thái</span>
        <span>CTV</span>
        <span>Link nhóm</span>
        <span>Ngày</span>
        <span>Xóa</span>
      </div>
      <div class="clean-table-empty">
        <strong>Đang tải dữ liệu...</strong>
      </div>
    </div>
  `;
}

function renderCtvLoadingTable() {
  const list = document.getElementById("orderList") || document.getElementById("ordersList");
  list.innerHTML = `
    <div class="clean-toolbar">
      <label class="clean-search">Tìm CTV
        <input id="ctvSearch" type="search" placeholder="ID, họ tên, Zalo...">
      </label>
      <label>Sắp xếp
        <select id="ctvSortBy">
          <option value="createdAt">Ngày tham gia</option>
          <option value="id">ID</option>
          <option value="name">Tên</option>
          <option value="recruited">Số UV đã tuyển</option>
        </select>
      </label>
      <label>Chiều
        <select id="ctvSortDirection">
          <option value="desc">Mới/Z-A</option>
          <option value="asc">Cũ/A-Z</option>
        </select>
      </label>
    </div>
    <div class="clean-table ctv-table">
      <div class="clean-table-head">
        <span>ID</span>
        <span>Họ tên</span>
        <span>Ngày tham gia</span>
        <span>Số UV đã tuyển</span>
        <span>Chi tiết</span>
        <span>Xóa</span>
      </div>
      <div class="clean-table-empty">
        <strong>Đang tải dữ liệu...</strong>
      </div>
    </div>
  `;
}

async function renderActiveSection() {
  const renderToken = ++sectionRenderToken;
  const section = activeSection;
  document.querySelectorAll("[data-orders-only]").forEach((item) => {
    item.hidden = section !== "orders";
  });
  const list = document.getElementById("orderList") || document.getElementById("ordersList");

  if (section === "orders") {
    renderOrders(currentOrders);
    return;
  }

  if (section === "candidates") {
    if (candidatesLoaded) {
      renderCandidateTable(currentCandidates);
      return;
    }
    if (!list.querySelector("#candidateSearch, .preload-candidates")) {
      renderCandidateLoadingTable();
    }
    const [candidates, applications] = await Promise.all([loadCandidates(), loadApplications()]);
    if (renderToken !== sectionRenderToken || activeSection !== section) return;
    currentCandidates = candidates;
    currentApplications = applications;
    candidatesLoaded = true;
    renderCandidateTable(currentCandidates);
    return;
  }

  if (ctvsLoaded) {
    renderCtvTable(currentCtvs);
    return;
  }
  if (!list.querySelector("#ctvSearch, .preload-ctvs")) {
    renderCtvLoadingTable();
  }
  const ctvs = await loadCtvs();
  if (renderToken !== sectionRenderToken || activeSection !== section) return;
  currentCtvs = ctvs;
  ctvsLoaded = true;
  renderCtvTable(currentCtvs);
}

async function refreshDashboard() {
  try {
    if (activeSection === "collaborators") {
      const [data, ctvs] = await Promise.all([loadDashboard(), loadCtvs()]);
      currentOrders = data.orders;
      currentCtvs = ctvs;
      ctvsLoaded = true;
      renderMetrics(data.metrics);
      renderCtvTable(currentCtvs);
      return;
    }

    const bootstrap = activeSection === "candidates" ? await loadBootstrap() : null;
    const data = bootstrap?.dashboard || await loadDashboard();
    currentOrders = data.orders;
    if (bootstrap && activeSection === "candidates") {
      currentCandidates = bootstrap.candidates || [];
      currentApplications = bootstrap.applications || [];
      currentCtvs = bootstrap.ctvs || [];
      candidatesLoaded = true;
      ctvsLoaded = true;
    }
    renderMetrics(data.metrics);
    await renderActiveSection();
  } catch (error) {
    const list = document.getElementById("orderList") || document.getElementById("ordersList");
    list.innerHTML = `
      <article class="order-card order-card-empty">
        <div class="empty-state">
          <strong>Lỗi tải dữ liệu</strong>
          <span>${escapeHtml(error.message)}</span>
        </div>
      </article>
    `;
  }
}

function bindNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");

      const section = button.dataset.section;
      activeSection = section;
      sectionRenderToken++;
      localStorage.setItem("activeDashboardSection", activeSection);
      document.documentElement.dataset.activeSection = activeSection;
      document.getElementById("pageTitle").textContent = sectionTitles[section].page;
      document.getElementById("primaryPanelTitle").textContent = sectionTitles[section].panel;
      updateTopbarCreateButton();
      renderActiveSection().catch((error) => {
        const list = document.getElementById("orderList") || document.getElementById("ordersList");
        list.innerHTML = `<div class="clean-empty-state"><strong>Lỗi tải dữ liệu</strong><span>${escapeHtml(error.message)}</span></div>`;
      });
    });
  });
}

function applyActiveNavigation() {
  if (!sectionTitles[activeSection]) activeSection = "orders";
  document.documentElement.dataset.activeSection = activeSection;
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.section === activeSection);
  });
  document.getElementById("pageTitle").textContent = sectionTitles[activeSection].page;
  document.getElementById("primaryPanelTitle").textContent = sectionTitles[activeSection].panel;
  updateTopbarCreateButton();
}

function updateTopbarCreateButton() {
  const button = document.getElementById("openCreateOrderModal");
  const label = button?.querySelector("span");
  if (!button || !label) return;

  if (activeSection === "candidates") {
    label.textContent = "Thêm UV";
    button.setAttribute("aria-label", "Thêm ứng viên");
  } else if (activeSection === "collaborators") {
    label.textContent = "Thêm CTV";
    button.setAttribute("aria-label", "Thêm cộng tác viên");
  } else {
    label.textContent = "Tạo đơn";
    button.setAttribute("aria-label", "Tạo đơn");
  }
}

function formatJsonForDetail(value) {
  try {
    return JSON.stringify(JSON.parse(value || "{}"), null, 2);
  } catch {
    return value || "{}";
  }
}

function openOrderDetail(order) {
  const modal = document.getElementById("orderDetailModal");
  const fallbackJobData = buildJobDataFromOrder(order);
  const fallbackText = makeShortText(fallbackJobData);
  const fallbackJson = JSON.stringify(buildJobJson(fallbackJobData), null, 2);
  const hasSavedJson = order.jobJson && order.jobJson !== "{}";
  const jsonText = hasSavedJson ? formatJsonForDetail(order.jobJson) : fallbackJson;
  const textUpFb = order.textUpFb || fallbackText || "Chưa có text up FB";
  const imageFileName = `${sanitizeFileName(`${order.code} - ${getOrderShortTitle(order)}`)}.${getImageExtension(order.imageDataUrl)}`;

  document.getElementById("detailModalTitle").textContent = `${order.code} - ${getOrderShortTitle(order)}`;
  document.getElementById("detailModalBody").innerHTML = `
    <div class="detail-box">
      <div class="detail-box-head">
        <strong>Hình ảnh</strong>
        ${order.imageDataUrl ? `<button class="detail-action-button" type="button" data-save-order-image="${escapeHtml(order.imageDataUrl)}" data-image-file-name="${escapeHtml(imageFileName)}">Lưu hình ảnh về máy</button>` : ""}
      </div>
      <div class="order-image">${order.imageDataUrl ? `<button class="image-preview-button" type="button" data-image-src="${escapeHtml(order.imageDataUrl)}"><img alt="Ảnh đơn ${escapeHtml(order.code)}" src="${escapeHtml(order.imageDataUrl)}"></button>` : "Chưa có hình ảnh"}</div>
    </div>
    <div class="detail-box">
      <div class="detail-box-head">
        <strong>Text up FB</strong>
        <button class="detail-action-button" type="button" data-copy-detail-text="${escapeHtml(textUpFb)}">Copy text</button>
      </div>
      <pre>${escapeHtml(textUpFb)}</pre>
    </div>
    <div class="detail-box">
      <strong>JSON database</strong>
      <pre>${escapeHtml(jsonText)}</pre>
    </div>
  `;
  modal.hidden = false;
}

function detailValue(value) {
  return escapeHtml(value || "Chưa có");
}

function openCtvDetail(ctv) {
  document.getElementById("detailModalTitle").textContent = `Chi tiết CTV - ${ctv.fullName || ctv.name || "Chưa có tên"}`;
  document.getElementById("detailModalBody").innerHTML = `
    <div class="detail-box person-detail-box">
      <dl class="detail-list ctv-detail-list">
        <div><dt>ID</dt><dd>${detailValue(formatShortId(ctv.id))}</dd></div>
        <div><dt>Họ tên</dt><dd>${detailValue(ctv.fullName || ctv.name)}</dd></div>
        <div><dt>Số điện thoại</dt><dd>${detailValue(ctv.phone)}</dd></div>
        <div><dt>Email</dt><dd>${detailValue(ctv.email)}</dd></div>
      </dl>
    </div>
  `;
  document.getElementById("orderDetailModal").hidden = false;
}

function closeOrderDetail() {
  document.getElementById("orderDetailModal").hidden = true;
}

function openImageLightbox(src) {
  document.getElementById("lightboxImage").src = src;
  document.getElementById("imageLightboxDialog").showModal();
}

function closeImageLightbox() {
  const dialog = document.getElementById("imageLightboxDialog");
  if (dialog.open) dialog.close();
  document.getElementById("lightboxImage").src = "";
}

function bindOrderDetailDialog() {
  const modal = document.getElementById("orderDetailModal");
  document.getElementById("closeOrderDetailButton").addEventListener("click", closeOrderDetail);
  document.getElementById("detailModalBody").addEventListener("click", (event) => {
    const saveImageButton = event.target.closest("[data-save-order-image]");
    if (saveImageButton) {
      if (saveImageButton.disabled) return;
      saveImageButton.disabled = true;
      downloadDataUrl(saveImageButton.dataset.saveOrderImage, saveImageButton.dataset.imageFileName || "order-image.png");
      saveImageButton.textContent = "Đã lưu";
      saveImageButton.classList.add("is-success");
      setTimeout(() => {
        saveImageButton.textContent = "Lưu hình ảnh về máy";
        saveImageButton.classList.remove("is-success");
        saveImageButton.disabled = false;
      }, 5000);
      return;
    }

    const copyTextButton = event.target.closest("[data-copy-detail-text]");
    if (copyTextButton) {
      if (copyTextButton.disabled) return;
      copyTextButton.disabled = true;
      navigator.clipboard.writeText(copyTextButton.dataset.copyDetailText || "").then(() => {
        copyTextButton.textContent = "Đã copy";
        copyTextButton.classList.add("is-success");
        setTimeout(() => {
          copyTextButton.textContent = "Copy text";
          copyTextButton.classList.remove("is-success");
          copyTextButton.disabled = false;
        }, 5000);
      }).catch((error) => {
        console.error(error);
        copyTextButton.disabled = false;
      });
      return;
    }

    const imageButton = event.target.closest("[data-image-src]");
    if (imageButton) openImageLightbox(imageButton.dataset.imageSrc);

    const deleteCtvButton = event.target.closest("[data-delete-ctv-id]");
    if (deleteCtvButton) {
      closeOrderDetail();
      openDeleteCtvDialog(deleteCtvButton.dataset.deleteCtvId, deleteCtvButton.dataset.deleteCtvName || "CTV");
    }
  });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeOrderDetail();
  });
  document.addEventListener("keydown", (event) => {
    const lightbox = document.getElementById("imageLightboxDialog");
    if (event.key === "Escape" && !modal.hidden && !lightbox.open) closeOrderDetail();
  });
}

function bindImageLightbox() {
  const dialog = document.getElementById("imageLightboxDialog");
  document.getElementById("closeImageLightbox").addEventListener("click", closeImageLightbox);
  dialog.addEventListener("click", (event) => {
    if (!event.target.closest("#lightboxImage")) closeImageLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open) {
      event.preventDefault();
      event.stopPropagation();
      closeImageLightbox();
    }
  });
}

function openDeleteDialog(order) {
  pendingDeleteCode = order.code;
  document.getElementById("deleteOrderText").textContent = `Xác nhận xóa đơn ${order.code}${order.title ? ` - ${order.title}` : ""}?`;
  document.getElementById("deleteOrderStatus").textContent = "";
  document.getElementById("confirmDeleteOrder").disabled = false;
  document.getElementById("cancelDeleteOrder").disabled = false;
  document.getElementById("deleteOrderDialog").showModal();
}

function closeDeleteDialog() {
  const dialog = document.getElementById("deleteOrderDialog");
  pendingDeleteCode = "";
  if (dialog.open) dialog.close();
}

function bindDeleteOrderDialog() {
  const dialog = document.getElementById("deleteOrderDialog");
  const confirmButton = document.getElementById("confirmDeleteOrder");
  const cancelButton = document.getElementById("cancelDeleteOrder");
  const status = document.getElementById("deleteOrderStatus");

  cancelButton.addEventListener("click", closeDeleteDialog);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDeleteDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open) closeDeleteDialog();
  });

  confirmButton.addEventListener("click", async () => {
    if (!pendingDeleteCode) return;
    confirmButton.disabled = true;
    cancelButton.disabled = true;
    status.textContent = "Đang xóa đơn...";

    try {
      await deleteOrder(pendingDeleteCode);
      status.textContent = "Đã xóa đơn.";
      await refreshDashboard();
      setTimeout(closeDeleteDialog, 450);
    } catch (error) {
      status.textContent = error.message;
      confirmButton.disabled = false;
      cancelButton.disabled = false;
    }
  });
}

function openDeleteCandidateDialog(candidateId, candidateName) {
  if (!candidateId) return;
  pendingDeleteCandidateId = candidateId;
  document.getElementById("deleteCandidateText").textContent = `Xác nhận xóa ${candidateName || "ứng viên"}?`;
  document.getElementById("deleteCandidateStatus").textContent = "";
  document.getElementById("confirmDeleteCandidate").disabled = false;
  document.getElementById("cancelDeleteCandidate").disabled = false;
  document.getElementById("deleteCandidateDialog").showModal();
}

function closeDeleteCandidateDialog() {
  const dialog = document.getElementById("deleteCandidateDialog");
  pendingDeleteCandidateId = "";
  if (dialog.open) dialog.close();
}

function bindDeleteCandidateDialog() {
  const dialog = document.getElementById("deleteCandidateDialog");
  const confirmButton = document.getElementById("confirmDeleteCandidate");
  const cancelButton = document.getElementById("cancelDeleteCandidate");
  const status = document.getElementById("deleteCandidateStatus");

  cancelButton.addEventListener("click", closeDeleteCandidateDialog);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDeleteCandidateDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open) closeDeleteCandidateDialog();
  });

  confirmButton.addEventListener("click", async () => {
    if (!pendingDeleteCandidateId) return;
    const candidateId = pendingDeleteCandidateId;
    closeDeleteCandidateDialog();
    confirmButton.disabled = true;
    cancelButton.disabled = true;
    status.textContent = "Đang xóa ứng viên...";

    try {
      await deleteCandidate(candidateId);
      currentCandidates = currentCandidates.filter((candidate) => candidate.id !== candidateId);
      currentApplications = currentApplications.filter((application) => application.candidateId !== candidateId);
      renderCandidateTable(currentCandidates);
    } catch (error) {
      console.error(error);
      pendingDeleteCandidateId = candidateId;
      status.textContent = error.message || "Không thể xóa ứng viên";
      if (!dialog.open) dialog.showModal();
      confirmButton.disabled = false;
      cancelButton.disabled = false;
    }
  });
}

function openDeleteCtvDialog(ctvId, ctvName) {
  if (!ctvId) return;
  pendingDeleteCtvId = ctvId;
  document.getElementById("deleteCtvText").textContent = `Xác nhận xóa ${ctvName || "CTV"}?`;
  document.getElementById("deleteCtvStatus").textContent = "";
  document.getElementById("confirmDeleteCtv").disabled = false;
  document.getElementById("cancelDeleteCtv").disabled = false;
  document.getElementById("deleteCtvDialog").showModal();
}

function closeDeleteCtvDialog() {
  const dialog = document.getElementById("deleteCtvDialog");
  pendingDeleteCtvId = "";
  if (dialog.open) dialog.close();
}

function bindDeleteCtvDialog() {
  const dialog = document.getElementById("deleteCtvDialog");
  const confirmButton = document.getElementById("confirmDeleteCtv");
  const cancelButton = document.getElementById("cancelDeleteCtv");
  const status = document.getElementById("deleteCtvStatus");

  cancelButton.addEventListener("click", () => {
    if (!cancelButton.disabled) closeDeleteCtvDialog();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && !cancelButton.disabled) closeDeleteCtvDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open && !cancelButton.disabled) closeDeleteCtvDialog();
  });

  confirmButton.addEventListener("click", async () => {
    if (!pendingDeleteCtvId) return;
    const ctvId = pendingDeleteCtvId;
    confirmButton.disabled = true;
    cancelButton.disabled = true;
    status.textContent = "Đang xóa CTV...";

    try {
      await deleteCtv(ctvId);
      currentCtvs = currentCtvs.filter((ctv) => ctv.id !== ctvId);
      ctvsLoaded = true;
      populateCandidateCtvOptions();
      updateActiveCollaboratorsMetric(currentCtvs.length);
      renderCtvTable(currentCtvs);
      closeDeleteCtvDialog();
    } catch (error) {
      console.error(error);
      pendingDeleteCtvId = ctvId;
      status.textContent = error.message || "Không thể xóa CTV";
      if (!dialog.open) dialog.showModal();
      confirmButton.disabled = false;
      cancelButton.disabled = false;
    }
  });
}

function setCreateDialogMode(mode) {
  const heading = document.querySelector("#createOrderDialog .modal-header h2");
  const submitLabel = document.querySelector('#createOrderForm button[type="submit"] span');
  if (mode === "edit") {
    heading.textContent = "Cập nhật nội dung đơn";
    submitLabel.textContent = "Cập nhật";
  } else {
    heading.textContent = "Tạo nội dung và lưu đơn";
    submitLabel.textContent = "Lưu đơn";
  }
}

function resetCreateOrderForm() {
  const form = document.getElementById("createOrderForm");
  form.reset();
  form.elements.headcount.value = 1;
  editingOrderCode = "";
  editingOrderSnapshot = null;
  imageDataReady = false;
  document.getElementById("rawOrderText").value = "";
  document.getElementById("imagePasteBox").textContent = "Bấm vào đây rồi Ctrl+V ảnh đơn";
  document.getElementById("imagePasteStatus").textContent = "Ảnh sẽ được dùng để đánh dấu đơn có hình ảnh.";
  document.getElementById("createOrderMessage").textContent = "";
  document.getElementById("createOrderMessage").className = "form-message";
  setCreateDialogMode("create");
  updateCreateImageBadges();
  updateCreatePreview();
}

function reopenOrder(order, options = {}) {
  const form = document.getElementById("createOrderForm");
  const dialog = document.getElementById("createOrderDialog");
  const submitButton = form.querySelector('button[type="submit"]');
  const isLoadingDetail = Boolean(options.loadingDetail);
  editingOrderCode = order.code;
  imageDataReady = Boolean(order.hasImageData);
  const fallbackJobData = buildJobDataFromOrder(order);
  const fallbackText = makeShortText(fallbackJobData);
  const fallbackJson = JSON.stringify(buildJobJson(fallbackJobData), null, 2);
  setCreateDialogMode("edit");

  form.elements.code.value = order.code || "";
  form.elements.title.value = order.title || "";
  form.elements.department.value = getOrderIndustries(order).join(", ") || order.department || "";
  form.elements.location.value = fallbackJobData.location !== "Chưa rõ" ? fallbackJobData.location : "";
  form.elements.headcount.value = order.headcount || 1;
  form.elements.status.value = order.status || "Chờ duyệt";
  document.getElementById("rawOrderText").value = order.rawText || buildRawTextFromOrder(order);

  if (order.imageDataUrl) {
    document.getElementById("imagePasteBox").innerHTML = `<img alt="Ảnh đơn đã paste" src="${escapeHtml(order.imageDataUrl)}">`;
    document.getElementById("imagePasteStatus").textContent = "Đã nạp ảnh đơn đã lưu. Có thể paste ảnh mới để thay thế.";
  } else {
    document.getElementById("imagePasteBox").textContent = "Bấm vào đây rồi Ctrl+V ảnh đơn";
    document.getElementById("imagePasteStatus").textContent = "Đơn này chưa có ảnh. Có thể paste ảnh để cập nhật.";
  }

  document.getElementById("facebookPreviewText").innerHTML = escapeHtml(order.textUpFb || fallbackText).replace(/\n/g, "<br>");
  document.getElementById("previewOrderCode").textContent = `Mã đơn: ${order.code || "--"}`;
  document.getElementById("createOrderJson").textContent = order.jobJson && order.jobJson !== "{}" ? formatJsonForDetail(order.jobJson) : fallbackJson;
  document.getElementById("createOrderMessage").textContent = isLoadingDetail ? "Đang tải dữ liệu đã lưu..." : "";
  document.getElementById("createOrderMessage").className = "form-message";
  updateCreateImageBadges();
  editingOrderSnapshot = isLoadingDetail ? null : buildOrderEditSnapshot(getCreateOrderFormData());
  if (submitButton) submitButton.disabled = isLoadingDetail;
  if (!dialog.open) dialog.showModal();
}

function readJsonValue(value, key) {
  try {
    return JSON.parse(value || "{}")[key] || "";
  } catch {
    return "";
  }
}

function buildJobDataFromOrder(order) {
  const savedLocation = readJsonValue(order.jobJson, "location");
  const savedRequirement = readJsonValue(order.jobJson, "requirement");
  const savedSalary = readJsonValue(order.jobJson, "salary_text");
  return {
    orderCode: order.code || "Chưa có",
    orderType: "Đơn tuyển",
    industry: getOrderIndustries(order)[0] || order.department || "Đơn tuyển",
    industries: getOrderIndustries(order),
    jobTitle: order.title || "Đơn tuyển",
    location: savedLocation || "Chưa rõ",
    company: readJsonValue(order.jobJson, "company") || "",
    salaryText: savedSalary || "Liên hệ",
    allowance: "",
    housing: readJsonValue(order.jobJson, "housing") || "",
    insurance: readJsonValue(order.jobJson, "insurance_benefits") || "",
    workingHours: readJsonValue(order.jobJson, "working_hours") || "",
    benefit: readJsonValue(order.jobJson, "benefits") || "",
    requirement: savedRequirement || "Liên hệ",
    japaneseLevel: readJsonValue(order.jobJson, "japanese_level") || "",
    salaryNumber: readJsonValue(order.jobJson, "salary_number") || "",
    salaryPeriod: readJsonValue(order.jobJson, "salary_period") || "",
    interview: readJsonValue(order.jobJson, "interview") || "Liên hệ",
    quantity: order.headcount || "",
    daysOff: readJsonValue(order.jobJson, "days_off") || "Liên hệ",
    work: readJsonValue(order.jobJson, "job_description") || order.title || "Đơn tuyển",
    back: readJsonValue(order.jobJson, "back_fee") || "",
    source: {
      image_ocr_used: Boolean(order.hasImageData),
      pasted_text_used: Boolean(order.rawText),
    },
  };
}

function buildRawTextFromOrder(order) {
  const jobData = buildJobDataFromOrder(order);
  return [
    `Mã đơn: ${jobData.orderCode}`,
    `Vị trí: ${jobData.jobTitle}`,
    `Nhóm ngành: ${(jobData.industries && jobData.industries.length ? jobData.industries : [jobData.industry]).filter(Boolean).join(", ")}`,
    jobData.location !== "Chưa rõ" ? `Khu vực: ${jobData.location}` : "",
    `Số lượng: ${jobData.quantity || order.headcount || 1}`,
    jobData.salaryText !== "Liên hệ" ? `Lương: ${jobData.salaryText}` : "",
    jobData.requirement !== "Liên hệ" ? `Yêu cầu: ${jobData.requirement}` : "",
  ].filter(Boolean).join("\n");
}

function bindCreateOrderModal() {
  const dialog = document.getElementById("createOrderDialog");
  const form = document.getElementById("createOrderForm");
  const message = document.getElementById("createOrderMessage");

  document.getElementById("openCreateOrderModal").addEventListener("click", () => {
    if (activeSection === "candidates") {
      openCreateCandidateModal().catch((error) => {
        console.error(error);
        alert(error.message || "Không thể mở form thêm ứng viên");
      });
      return;
    }
    if (activeSection === "collaborators") {
      openCreateCtvModal();
      return;
    }
    resetCreateOrderForm();
    dialog.showModal();
  });

  document.getElementById("closeCreateOrderModal").addEventListener("click", () => {
    dialog.close();
  });

  document.querySelector("[data-close-modal]").addEventListener("click", () => {
    dialog.close();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  document.getElementById("parseOrderButton").addEventListener("click", parseOrderText);

  document.getElementById("clearCreateOrderButton").addEventListener("click", () => {
    resetCreateOrderForm();
  });

  document.getElementById("copyPreviewTextButton").addEventListener("click", async () => {
    const text = document.getElementById("facebookPreviewText").innerText.trim();
    if (!text) return;
    await navigator.clipboard.writeText(text);
    document.getElementById("copyPreviewStatus").textContent = "Đã copy";
    setTimeout(() => {
      document.getElementById("copyPreviewStatus").textContent = "";
    }, 1600);
  });

  document.getElementById("imagePasteBox").addEventListener("paste", (event) => {
    const item = [...event.clipboardData.items].find((entry) => entry.type.startsWith("image/"));
    if (!item) return;
    const file = item.getAsFile();
    const reader = new FileReader();
    reader.onload = () => {
      document.getElementById("imagePasteBox").innerHTML = `<img alt="Ảnh đơn đã paste" src="${reader.result}">`;
      document.getElementById("imagePasteStatus").textContent = "Đã nhận ảnh đơn.";
      imageDataReady = false;
      updateCreateImageBadges();
      updateCreatePreview();
    };
    reader.readAsDataURL(file);
  });

  form.querySelectorAll("input, select, textarea").forEach((field) => {
    field.addEventListener("input", updateCreatePreview);
    field.addEventListener("change", updateCreatePreview);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    const missingFields = getMissingOrderFields(form);
    if (missingFields.length) {
      showRequiredFieldsMessage(message, missingFields);
      const firstMissing = form.querySelector("[required]:not([type='hidden']):invalid") || form.querySelector("[required]:not([type='hidden'])");
      firstMissing?.focus();
      return;
    }
    const payload = getCreateOrderFormData();

    if (editingOrderCode && !hasComparableChanges(editingOrderSnapshot, buildOrderEditSnapshot(payload))) {
      message.textContent = "Chưa có thay đổi để cập nhật.";
      message.className = "form-message";
      return;
    }

    message.textContent = "Đang lưu đơn...";
    message.className = "form-message";
    submitButton.disabled = true;

    try {
      if (editingOrderCode) {
        await updateOrder(editingOrderCode, payload);
        message.textContent = "Cập nhật thành công";
      } else {
        await createOrder(payload);
        message.textContent = "Đã tạo đơn tuyển dụng.";
      }
      message.className = "form-message success";
      await refreshDashboard();
      setTimeout(() => {
        dialog.close();
        resetCreateOrderForm();
      }, 650);
    } catch (error) {
      message.textContent = error.message;
      message.className = "form-message error";
    } finally {
      submitButton.disabled = false;
    }
  });
}

function resetCreateCandidateForm() {
  const form = document.getElementById("createCandidateForm");
  form.reset();
  form.elements.stage.value = "Chờ PV";
  document.getElementById("candidateStagePreview").value = "Chờ PV";
  syncCandidateOrderFields();
  syncCandidateCtvFields();
  const message = document.getElementById("createCandidateMessage");
  message.textContent = "";
  message.className = "form-message";
}

function setCandidateDialogMode(mode) {
  const isEdit = mode === "edit";
  document.getElementById("candidateModalTitle").textContent = isEdit ? "Chỉnh sửa ứng viên" : "Thêm ứng viên";
  document.getElementById("candidateModalDescription").textContent = isEdit
    ? "Cập nhật thông tin ứng viên đang chọn."
    : "Nhập thông tin ứng viên mới và lưu trực tiếp vào database.";
  document.getElementById("candidateSubmitLabel").textContent = isEdit ? "Cập nhật UV" : "Lưu UV";
}

async function openCreateCandidateModal() {
  editingCandidateId = "";
  editingApplicationId = "";
  editingCandidateSnapshot = null;
  setCandidateDialogMode("add");
  resetCreateCandidateForm();
  await prepareCandidateModalOptions();
  document.getElementById("createCandidateDialog").showModal();
}

async function openEditCandidateModal(candidateId) {
  const candidate = currentCandidates.find((item) => item.id === candidateId);
  if (!candidate) return;
  const application = getCandidateApplication(candidate);
  const form = document.getElementById("createCandidateForm");
  const status = getCandidateStatus(candidate, application);

  editingCandidateId = candidate.id;
  editingApplicationId = application?.id || "";
  setCandidateDialogMode("edit");
  resetCreateCandidateForm();
  await prepareCandidateModalOptions();
  const order = currentOrders.find((item) => item.id === application?.orderId || item.code === application?.orderCode);
  const ctv = currentCtvs.find((item) => item.id === application?.ctvId);

  form.elements.fullName.value = candidate.fullName || candidate.name || "";
  form.elements.phone.value = candidate.phone || "";
  form.elements.email.value = candidate.email || "";
  form.elements.groupLink.value = getCandidateGroupLink(candidate, application) || "";
  form.elements.stage.value = status;
  document.getElementById("candidateStagePreview").value = status;
  document.getElementById("candidateOrderSearch").value = order ? getOrderSearchLabel(order) : "";
  document.getElementById("candidateCtvSearch").value = ctv ? getCtvSearchLabel(ctv) : application?.sourceNote || candidate.source || "";
  syncCandidateOrderFields();
  syncCandidateCtvFields();
  editingCandidateSnapshot = buildCandidateEditSnapshot(
    Object.fromEntries(new FormData(form).entries()),
    syncCandidateOrderFields(),
    syncCandidateCtvFields(),
  );
  document.getElementById("createCandidateDialog").showModal();
}

function closeCreateCandidateModal() {
  const dialog = document.getElementById("createCandidateDialog");
  if (dialog.open) dialog.close();
}

function bindCreateCandidateModal() {
  const dialog = document.getElementById("createCandidateDialog");
  const form = document.getElementById("createCandidateForm");
  const message = document.getElementById("createCandidateMessage");

  document.getElementById("closeCreateCandidateModal").addEventListener("click", closeCreateCandidateModal);
  document.getElementById("cancelCreateCandidate").addEventListener("click", closeCreateCandidateModal);
  document.getElementById("candidateOrderSearch").addEventListener("input", syncCandidateOrderFields);
  document.getElementById("candidateOrderSearch").addEventListener("change", syncCandidateOrderFields);
  document.getElementById("candidateCtvSearch").addEventListener("input", syncCandidateCtvFields);
  document.getElementById("candidateCtvSearch").addEventListener("change", syncCandidateCtvFields);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeCreateCandidateModal();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    const selectedOrder = syncCandidateOrderFields();
    const selectedCtv = syncCandidateCtvFields();
    const payload = Object.fromEntries(new FormData(form).entries());
    const missingFields = getMissingCandidateFields(form, selectedOrder, selectedCtv);

    if (missingFields.length) {
      showRequiredFieldsMessage(message, missingFields);
      if (!form.elements.fullName.value.trim()) {
        form.elements.fullName.focus();
      } else if (!selectedOrder?.id) {
        document.getElementById("candidateOrderSearch").focus();
      } else if (!selectedCtv?.id) {
        document.getElementById("candidateCtvSearch").focus();
      } else {
        form.elements.fullName.focus();
      }
      return;
    }

    if (payload.phone?.trim() && !hasValidPhoneCharacters(payload.phone)) {
      message.textContent = "SĐT ứng viên chỉ được nhập số.";
      message.className = "form-message error";
      form.elements.phone.focus();
      return;
    }

    if (payload.phone?.trim() && !isValidVietnamMobile(payload.phone)) {
      message.textContent = "SĐT ứng viên không đúng định dạng. Vui lòng nhập số di động Việt Nam 10 số.";
      message.className = "form-message error";
      form.elements.phone.focus();
      return;
    }

    if (payload.email?.trim() && !isValidEmail(payload.email)) {
      message.textContent = "Email ứng viên không đúng định dạng.";
      message.className = "form-message error";
      form.elements.email.focus();
      return;
    }

    try {
      payload.stage = editingCandidateId ? payload.stage || "Chờ PV" : "Chờ PV";
      if (editingCandidateId) {
        const currentSnapshot = buildCandidateEditSnapshot(payload, selectedOrder, selectedCtv);
        if (!hasComparableChanges(editingCandidateSnapshot, currentSnapshot)) {
          message.textContent = "Không có thay đổi để cập nhật.";
          message.className = "form-message";
          submitButton.disabled = false;
          return;
        }
      }
      const candidateId = editingCandidateId;
      const wasEditing = Boolean(editingCandidateId);

      message.textContent = "";
      message.className = "form-message";
      submitButton.disabled = true;
      closeCreateCandidateModal();

      if (editingCandidateId) {
        await updateCandidate(editingCandidateId, payload);
      } else {
        const candidate = await createCandidate(payload);
        payload.stage = "Chờ PV";
        editingApplicationId = "";
        editingCandidateId = candidate.id;
      }

      if (selectedOrder?.id) {
        const applicationPayload = {
          orderId: selectedOrder.id,
          candidateId: candidateId || editingCandidateId,
          ctvId: selectedCtv?.id || "",
          stage: payload.stage || "Chờ PV",
          role: getOrderIndustryLabel(selectedOrder),
          sourceNote: selectedCtv ? (selectedCtv.fullName || selectedCtv.name || "") : payload.source || "",
          groupLink: payload.groupLink || "",
          note: payload.note || "",
        };
        if (editingApplicationId) {
          await updateApplication(editingApplicationId, applicationPayload);
        } else {
          await createApplication(applicationPayload);
        }
      }

      message.textContent = wasEditing ? "Đã cập nhật ứng viên." : "Đã thêm ứng viên.";
      message.className = "form-message success";
      candidatesLoaded = false;
      resetCreateCandidateForm();
      refreshDashboard().catch((error) => {
        console.error(error);
        message.textContent = error.message;
        message.className = "form-message error";
      });
    } catch (error) {
      message.textContent = error.message;
      message.className = "form-message error";
      if (!dialog.open) dialog.showModal();
    } finally {
      submitButton.disabled = false;
    }
  });
}

function formatInterviewDate(value) {
  if (!value) return "Chưa có lịch PV";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getDate()}/${date.getMonth() + 1}/${date.getFullYear()}`;
}

function formatDateInputValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return date.toISOString().slice(0, 10);
}

function normalizeInterviewLink(value) {
  const link = String(value || "").trim();
  if (!link || link === "https://..." || link === "http://...") return "";
  return /^https?:\/\//i.test(link) ? link : `https://${link}`;
}

function setInterviewEditMode(isEditing) {
  document.getElementById("interviewScheduleDate").hidden = isEditing;
  document.getElementById("interviewScheduleLink").hidden = isEditing;
  document.getElementById("interviewScheduleDateInput").hidden = !isEditing;
  document.getElementById("interviewScheduleLinkInput").hidden = !isEditing;
  document.getElementById("interviewScheduleActions").hidden = !isEditing;
  document.getElementById("editInterviewSchedule").hidden = isEditing;
  document.getElementById("interviewScheduleStatus").textContent = "";
  document.getElementById("interviewScheduleStatus").className = "form-message";
}

function openInterviewScheduleDialog(applicationId, candidateId) {
  const dialog = document.getElementById("interviewScheduleDialog");
  const application = currentApplications.find((item) => item.id === applicationId)
    || currentApplications.find((item) => item.candidateId === candidateId)
    || null;
  const candidate = currentCandidates.find((item) => item.id === candidateId)
    || currentCandidates.find((item) => item.id === application?.candidateId)
    || {};
  const title = candidate.fullName || candidate.name || application?.candidateName || "Ứng viên";
  const link = application?.interviewLink || application?.interviewUrl || application?.meetingLink || "";
  const linkElement = document.getElementById("interviewScheduleLink");
  activeInterviewApplicationId = application?.id || "";

  document.getElementById("interviewScheduleTitle").textContent = `Lịch PV - ${title}`;
  document.getElementById("interviewScheduleDate").textContent = formatInterviewDate(application?.interviewAt);
  document.getElementById("interviewScheduleDateInput").value = formatDateInputValue(application?.interviewAt);
  document.getElementById("interviewScheduleLinkInput").value = link;
  linkElement.textContent = link ? "Mở link PV" : "Chưa có link PV";
  linkElement.href = link || "#";
  linkElement.toggleAttribute("aria-disabled", !link);
  linkElement.classList.toggle("is-disabled", !link);
  setInterviewEditMode(false);
  dialog.showModal();
}

function closeInterviewScheduleDialog() {
  const dialog = document.getElementById("interviewScheduleDialog");
  if (dialog.open) dialog.close();
  activeInterviewApplicationId = "";
  setInterviewEditMode(false);
}

async function saveInterviewSchedule() {
  const application = currentApplications.find((item) => item.id === activeInterviewApplicationId);
  const status = document.getElementById("interviewScheduleStatus");
  if (!application) {
    status.textContent = "Không tìm thấy lượt ứng tuyển.";
    status.className = "form-message error";
    return;
  }

  const interviewAt = document.getElementById("interviewScheduleDateInput").value;
  const interviewLink = normalizeInterviewLink(document.getElementById("interviewScheduleLinkInput").value);
  const currentInterviewAt = formatDateInputValue(application.interviewAt);
  const currentInterviewLink = normalizeInterviewLink(application.interviewLink || application.interviewUrl || application.meetingLink || "");

  if (!interviewAt && !interviewLink) {
    status.textContent = "Vui lòng nhập ngày PV hoặc link PV trước khi lưu.";
    status.className = "form-message error";
    return;
  }

  if (interviewLink) {
    try {
      new URL(interviewLink);
    } catch {
      status.textContent = "Link PV không hợp lệ.";
      status.className = "form-message error";
      return;
    }
  }

  if (interviewAt === currentInterviewAt && interviewLink === currentInterviewLink) {
    status.textContent = "Chưa có thay đổi để lưu.";
    status.className = "form-message";
    return;
  }

  const payload = {
    orderId: application.orderId,
    candidateId: application.candidateId,
    ctvId: application.ctvId || "",
    stage: application.stage || "Chờ PV",
    status: application.status || "Đang xử lý",
    sourceType: application.sourceType || "CTV",
    sourceNote: application.sourceNote || application.ctvName || "",
    groupLink: application.groupLink || "",
    role: application.role || "",
    note: application.note || "",
    interviewAt,
    interviewLink,
  };

  status.textContent = "Đang lưu lịch PV...";
  status.className = "form-message";
  try {
    await updateApplication(application.id, payload);
    application.interviewAt = interviewAt;
    application.interviewLink = interviewLink;
    document.getElementById("interviewScheduleDate").textContent = formatInterviewDate(interviewAt);
    const linkElement = document.getElementById("interviewScheduleLink");
    linkElement.textContent = interviewLink ? "Mở link PV" : "Chưa có link PV";
    linkElement.href = interviewLink || "#";
    linkElement.toggleAttribute("aria-disabled", !interviewLink);
    linkElement.classList.toggle("is-disabled", !interviewLink);
    status.textContent = "Đã lưu lịch PV.";
    status.className = "form-message success";
    setInterviewEditMode(false);
  } catch (error) {
    status.textContent = error.message;
    status.className = "form-message error";
  }
}

function bindInterviewScheduleDialog() {
  const dialog = document.getElementById("interviewScheduleDialog");
  document.getElementById("closeInterviewSchedule").addEventListener("click", closeInterviewScheduleDialog);
  document.getElementById("editInterviewSchedule").addEventListener("click", () => setInterviewEditMode(true));
  document.getElementById("cancelInterviewEdit").addEventListener("click", () => setInterviewEditMode(false));
  document.getElementById("saveInterviewSchedule").addEventListener("click", saveInterviewSchedule);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeInterviewScheduleDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open) closeInterviewScheduleDialog();
  });
}

function openCreateCtvModal() {
  const form = document.getElementById("createCtvForm");
  const message = document.getElementById("createCtvMessage");
  form.reset();
  message.textContent = "";
  message.className = "form-message";
  document.getElementById("createCtvDialog").showModal();
  form.elements.fullName.focus();
}

function closeCreateCtvModal() {
  const dialog = document.getElementById("createCtvDialog");
  if (dialog.open) dialog.close();
}

function bindCreateCtvModal() {
  const dialog = document.getElementById("createCtvDialog");
  const form = document.getElementById("createCtvForm");
  const message = document.getElementById("createCtvMessage");
  const submitButton = form.querySelector('button[type="submit"]');

  document.getElementById("closeCreateCtvModal").addEventListener("click", closeCreateCtvModal);
  document.getElementById("cancelCreateCtv").addEventListener("click", closeCreateCtvModal);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeCreateCtvModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog.open) closeCreateCtvModal();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    const missingFields = [];
    if (!payload.fullName?.trim()) missingFields.push("Họ tên");
    if (!payload.phone?.trim()) missingFields.push("Số điện thoại");

    if (missingFields.length) {
      message.textContent = `Vui lòng nhập/chọn: ${missingFields.join(", ")}.`;
      message.className = "form-message error";
      if (!payload.fullName?.trim()) {
        form.elements.fullName.focus();
      } else {
        form.elements.phone.focus();
      }
      return;
    }

    if (!hasValidPhoneCharacters(payload.phone)) {
      message.textContent = "SĐT CTV chỉ được nhập số.";
      message.className = "form-message error";
      form.elements.phone.focus();
      return;
    }

    if (!isValidVietnamMobile(payload.phone)) {
      message.textContent = "SĐT CTV không đúng định dạng. Vui lòng nhập số di động Việt Nam 10 số.";
      message.className = "form-message error";
      form.elements.phone.focus();
      return;
    }

    if (payload.email?.trim() && !isValidEmail(payload.email)) {
      message.textContent = "Email CTV không đúng định dạng.";
      message.className = "form-message error";
      form.elements.email.focus();
      return;
    }

    message.textContent = "Đang lưu CTV...";
    message.className = "form-message";
    submitButton.disabled = true;
    try {
      await createCtv(payload);
      currentCtvs = await loadCtvs();
      ctvsLoaded = true;
      populateCandidateCtvOptions();
      renderCtvTable(currentCtvs);
      message.textContent = "Đã thêm CTV.";
      message.className = "form-message success";
      setTimeout(closeCreateCtvModal, 450);
    } catch (error) {
      message.textContent = error.message;
      message.className = "form-message error";
    } finally {
      submitButton.disabled = false;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  applyActiveNavigation();
  bindNavigation();
  bindOrderDetailDialog();
  bindImageLightbox();
  bindDeleteOrderDialog();
  bindDeleteCandidateDialog();
  bindDeleteCtvDialog();
  bindCreateOrderModal();
  bindCreateCandidateModal();
  bindCreateCtvModal();
  bindInterviewScheduleDialog();

  ["orderSearch", "orderSortBy", "orderSortDirection"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => renderOrders(currentOrders));
    document.getElementById(id)?.addEventListener("change", () => renderOrders(currentOrders));
  });

  refreshDashboard();

  if (window.lucide) {
    window.lucide.createIcons();
  }
});
