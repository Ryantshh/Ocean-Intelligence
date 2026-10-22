const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('ai_platform/dashboard/index.html', 'utf8');
const code = html.slice(html.indexOf('      function writeToChatComposer(text) {'), html.indexOf('      async function sendOrdersToChat()'));
let ready = true, callback, cleared = false;
class Textarea {
  get value() { return this.text ?? ''; }
  set value(value) { this.text = value; }
  dispatchEvent() { this.notified = true; }
  focus() {}
}
const input = new Textarea();
input.tagName = 'TEXTAREA';
input.value = 'Existing draft';
const banner = {};
const frameWindow = { document: {querySelector: () => ready ? input : null}, HTMLTextAreaElement: Textarea, Event: class {} };
const context = vm.createContext({
  document: { getElementById: id => id === 'chatFrame' ? {contentWindow: frameWindow} : banner, querySelector: () => ({click() {}}) },
  setChatOpen: () => {},
  setInterval: fn => { callback = fn; return 1; },
  clearInterval: () => { cleared = true; },
  setTimeout: () => {}, Date,
});
vm.runInContext(code, context);
context.queueChatPrompt('Order 123');
assert.equal(input.value, 'Existing draft\n\nOrder 123');
assert.ok(input.notified);
ready = false;
context.queueChatPrompt('Order 456');
assert.match(banner.textContent, /Sign in/);
assert.equal(input.value, 'Existing draft\n\nOrder 123');
ready = true;
callback();
assert.equal(input.value, 'Existing draft\n\nOrder 123\n\nOrder 456');
assert.ok(cleared);
callback();
assert.equal(input.value.split('Order 456').length, 2);
console.log('Chat handoff: draft preservation, delayed readiness and single delivery passed.');
