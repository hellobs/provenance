/* English presentation for the live scene and its embedded review pages.
 * Historical records may contain Chinese text. Keep that text out of the UI
 * until an English record is available, while translating known interface copy.
 */
(function () {
  "use strict";
  var han = /[\u3400-\u9fff]/;
  var translations = {
    "结果记录": "Results and Records",
    "专家审核": "Expert Review",
    "配置工具": "Configuration Tool",
    "运行场景(小镇)": "Live Simulation",
    "当前场景": "Current Scene",
    "数据界面": "Data Explorer",
    "重开一局": "Start New Run",
    "单独打开": "Open Separately",
    "连接中": "Connecting",
    "已连接": "Connected",
    "连接已断开": "Connection lost",
    "连接出错": "Connection error",
    "推演进行中": "Simulation running",
    "推演已结束": "Simulation finished",
    "推演结束": "Simulation finished",
    "推演出错": "Simulation error",
    "推演被中断": "Simulation interrupted",
    "仅审阅模式": "Review mode",
    "没有在推演": "No simulation is running",
    "结果生成中": "Generating results",
    "结果已生成": "Results ready",
    "正在重开": "Starting a new run",
    "已重开": "New run started",
    "重开失败": "Restart failed",
    "重开请求失败": "Restart request failed",
    "未知原因": "Unknown reason",
    "历史数据一览": "Historical Runs",
    "历史记录": "historical runs",
    "显示全部记录": "Show all records",
    "加载详情中": "Loading details",
    "加载中": "Loading",
    "成品 run": "Final run",
    "成品记录": "final record",
    "运行时痕迹": "Runtime trace",
    "压缩成品": "Compressed record",
    "合格": "Passed",
    "未质检": "Not reviewed",
    "存疑": "Questionable",
    "调试跑": "Debug run",
    "已废弃": "Deprecated",
    "未落盘": "Not saved",
    "未归档数据": "Uncategorized data",
    "场景": "Scenario",
    "运行方式": "Run type",
    "分支来源": "Branch source",
    "分支": "Branch",
    "质检标记": "Quality label",
    "一致性": "Consistency",
    "可复现情况": "Reproducibility",
    "开始时间": "Start time",
    "结束时间": "End time",
    "对话轮次": "Conversation turns",
    "反思条数": "Reflections",
    "数据完整性": "Data completeness",
    "完整": "Complete",
    "概览": "Overview",
    "摘要": "Summary",
    "反思": "Reflection",
    "对话": "Conversation",
    "事件": "Events",
    "状态": "State",
    "检索": "Retrieval",
    "审计": "Audit",
    "问题分流": "Issue routing",
    "倾向": "Tendency",
    "轨迹": "Trajectory",
    "快照": "Snapshot",
    "风险": "Risk",
    "内容文件": "Content files",
    "汇总": "Summary",
    "总计": "Total",
    "默认质检过滤": "Filtered by default quality rules",
    "节点": "Node",
    "实时": "Live",
    "空": "Empty",
    "没有": "None",
    "未校验": "Not checked",
    "一致": "Consistent",
    "不一致": "Inconsistent",
    "判不了": "Undetermined",
    "日期区间": "Date range",
    "判定方式": "Decision method",
    "专家": "Expert",
    "审核": "Review",
    "提交中": "Submitting",
    "已写入": "Saved",
    "认可": "Approve",
    "建议修改": "Suggest revision",
    "不成立": "Reject",
    "嗯": "[Model response unavailable]",
    "盘面": "Market tape",
    "市场": "Market",
    "行业媒体": "Industry media",
    "机构研究": "Institutional research",
    "行为金融学": "Behavioral finance",
    "市场情绪分析": "Market sentiment analysis",
    "估值建模与投资分析": "Valuation modeling and investment analysis",
    "HCM 公告": "HCM announcement",
    "收盘": "Close",
    "当日": "That day",
    "区间": "Range",
    "HCM 公告确认正与国际新能源汽车制造商开展高镍正极材料产品验证及商务沟通。": "HCM announced product validation and business discussions with an international electric vehicle manufacturer about high-nickel cathode materials.",
    "市场围绕『120-150 亿元潜在订单』快速发酵;10:30 约 $42.60,收盘 $45.80,成交明显放大。": "Market speculation about a potential CNY 12–15 billion order intensified. The price was about $42.60 at 10:30 and closed at $45.80 on higher volume.",
    "市场继续关注验证进展;无新正式公告,行业媒体继续报道项目推进。收盘 $47.30。": "The market kept watching validation progress. There was no new official announcement, while industry media continued to report on the project. The stock closed at $47.30.",
    "多家机构更新盈利预测;关注点转向实际供应份额/新产线利用率/客户是否扩大采购。收盘 $60.20。": "Several institutions updated earnings forecasts. Attention shifted to the actual supply share, use of new production lines, and whether the customer would expand purchases. The stock closed at $60.20.",
    "HCM 公告:客户已完成本阶段主要产品测试,产品达到进入下一阶段供应商评估要求;公司开始就供应安排/交付能力/商务条件进一步沟通。最终采购数量及长期规模仍未定。": "HCM announced that the customer completed the main product tests for this stage and the product qualified for the next supplier assessment stage. Discussions on supply, delivery capacity, and commercial terms began. Final purchase quantities and long-term scale remain undecided.",
    "HCM 公告:被纳入该客户下一阶段合格供应商名单并签署初步供应安排;首阶段采购规模明显低于『120-150 亿元』乐观测算,但对当前业务规模有实质意义;首批供货预计 Q4 开始。收盘 $57.40。": "HCM announced inclusion on the customer's qualified supplier list and a preliminary supply arrangement. Initial purchases are well below the optimistic CNY 12–15 billion estimate but meaningful for the current business. First deliveries are expected in Q4. The stock closed at $57.40.",
    "首批供应计划进一步明确,情绪趋稳;项目已转化为正式合作,但规模未达 120-150 亿元传闻。股价稳定 $59-61。相对 T0 $42.60 累计上涨约 40%。": "The initial supply plan became clearer and sentiment stabilized. The project became a formal partnership, though its scale fell short of the rumored CNY 12–15 billion. The price stabilized at $59–61, roughly 40% above the T0 price of $42.60.",
    "待领取": "Unassigned",
    "争议轮": "Dispute round",
    "等第二份": "Awaiting second review",
    "首轮已齐": "First round complete",
    "条": " items",
    "轮": " turns",
    "个": " items",
    "份": " submissions"
  };
  var entries = Object.keys(translations).sort(function (a, b) { return b.length - a.length; });

  function englishDisplay(value) {
    var source = String(value == null ? "" : value);
    if (!han.test(source)) return source;
    var result = source;
    entries.forEach(function (word) {
      if (result.indexOf(word) !== -1) result = result.split(word).join(translations[word]);
    });
    if (han.test(result)) result = result.replace(
      /[\u3400-\u9fff]+(?:[\s，。；：、？！“”‘’（）【】]*[\u3400-\u9fff]+)*/g,
      "[Legacy passage unavailable in English]"
    );
    return result;
  }
  window.englishDisplay = englishDisplay;

  function updateText(root) {
    if (!root || !document.createTreeWalker) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      var parent = node.parentElement;
      if (parent && /^(SCRIPT|STYLE|NOSCRIPT)$/.test(parent.tagName)) continue;
      if (han.test(node.nodeValue)) node.nodeValue = englishDisplay(node.nodeValue);
    }
    var elements = root.querySelectorAll ? Array.prototype.slice.call(root.querySelectorAll("[title],[placeholder],[aria-label],[alt],[value]")) : [];
    if (root.matches && root.matches("[title],[placeholder],[aria-label],[alt],[value]")) elements.unshift(root);
    Array.prototype.forEach.call(elements, function (el) {
      ["title", "placeholder", "aria-label", "alt"].forEach(function (attr) {
        var value = el.getAttribute(attr);
        if (value && han.test(value)) el.setAttribute(attr, englishDisplay(value));
      });
      if (el.tagName === "INPUT" && /^(button|submit|reset)$/i.test(el.type) && han.test(el.value)) {
        el.value = englishDisplay(el.value);
      }
    });
  }

  function patchPhaser() {
    if (!window.Phaser || !Phaser.GameObjects || !Phaser.GameObjects.Text) return false;
    var prototype = Phaser.GameObjects.Text.prototype;
    if (prototype.__englishOnly) return true;
    var original = prototype.setText;
    prototype.setText = function (value) {
      if (Array.isArray(value)) value = value.map(englishDisplay);
      else value = englishDisplay(value);
      return original.call(this, value);
    };
    prototype.__englishOnly = true;
    return true;
  }
  window.enforceEnglishPhaser = patchPhaser;

  function start() {
    updateText(document.body);
    new MutationObserver(function (changes) {
      changes.forEach(function (change) {
        if (change.type === "characterData") updateText(change.target.parentElement);
        else updateText(change.target);
      });
    }).observe(document.body, {
      childList: true, characterData: true, attributes: true,
      attributeFilter: ["title", "placeholder", "aria-label", "alt", "value"], subtree: true
    });
    if (!patchPhaser()) {
      var timer = setInterval(function () { if (patchPhaser()) clearInterval(timer); }, 100);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
