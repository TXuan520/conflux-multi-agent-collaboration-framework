/* SentenceBackbone 1.0.0 — dependency-free, browser + CommonJS. */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.SentenceBackbone = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const actions = '制作|开发|实现|搭建|创建|编写|设计|修改|修复|优化|增加|添加|删除|移除|更新|接入|提取|生成|写|做|建|改';
  const actionStart = new RegExp('^(?:' + actions + ')');
  const lead = /^(?:(?:麻烦|请|帮忙|帮我|给我|替我|劳驾|能不能|可不可以|能否|可以|我要|我想要|我想|我需要|想要|需要|你|您|先|再|来)\s*)+/;

  /** Return a compact task title; never executes work or writes tickets. */
  function extract(input) {
    if (typeof input !== 'string') return '';
    let value = input.replace(/^\s*【[^】\r\n]{1,40}】[：:]\s*/, '')
      .replace(/^\s*>>>\s*[^\r\n]+[\r\n]+/, '')
      .replace(/\s+/g, ' ').trim();
    if (!value) return '';
    // Descriptions after these markers belong in the full ticket body.
    value = value.split(/[，,。；;！!？?\n]\s*(?:(?:具体)?要求(?:是|就是|如下)?|例如|比如|功能(?:包括|是)|需要支持|支持|界面|风格|然后|接着|不需要|不用|不要|只要|记得|颜色|用(?:户|什么)|采用)/)[0];
    value = value.split(/[。！!？?]/)[0].trim();
    // Remove polite wrappers repeatedly, including “我想让你帮我…”.
    for (let i = 0; i < 5; i++) {
      const before = value;
      value = value.replace(lead, '').replace(/^(?:让你|让您)\s*/, '');
      if (value === before) break;
    }
    // Only remove a classifier immediately following the action.
    value = value.replace(new RegExp('^(' + actions + ')\\s*(?:一个|一款|一套|一份|一项|个|款)\\s*'), '$1');
    value = value.replace(/小游戏/g, '游戏');
    value = value.replace(/(?:可以吗|行不行|好不好|好吗|谢谢你|谢谢|吧|呀|啊|呢|吗)[，,。！!？?\s]*$/, '')
      .replace(/[，,。；;！!？?\s]+$/, '').trim();
    // Do not invent an action when the user only supplied a noun phrase.
    return value;
  }

  function analyze(input) {
    const title = extract(input);
    return {
      title,
      original: typeof input === 'string' ? input : '',
      hasAction: actionStart.test(title),
      method: 'rules',
      needsReview: !title || !actionStart.test(title) || /(?:不做|不要|不用|别|取消|不是|而是)/.test(title)
    };
  }

  return Object.freeze({ id: 'sentence-backbone', version: '1.0.0', extract, analyze });
});
