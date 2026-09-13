const CHUNK = 2 * 1024 * 1024;

async function readFileInChunks(file, onProgress) {
  const reader = file.stream().getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let totalRead = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    totalRead += value.byteLength;
    if (onProgress) onProgress(totalRead, file.size);
  }
  return buffer;
}

function extractArrayObjects(text, arrayKey) {
  const results = [];
  const marker = `"${arrayKey}"`;
  let idx = text.indexOf(marker);
  if (idx === -1) return results;

  idx += marker.length;
  while (idx < text.length) {
    const c = text[idx];
    if (c === ':') { idx++; break; }
    if (c !== ' ' && c !== '\n' && c !== '\r' && c !== '\t') break;
    idx++;
  }
  while (idx < text.length && (text[idx] === ' ' || text[idx] === '\n' || text[idx] === '\r' || text[idx] === '\t')) idx++;
  if (text[idx] !== '[') return results;
  idx++;

  let depth = 0;
  let objStart = -1;
  let inString = false;
  let escape = false;

  for (; idx < text.length; idx++) {
    const c = text[idx];
    if (inString) {
      if (escape) escape = false;
      else if (c === '\\') escape = true;
      else if (c === '"') inString = false;
    } else {
      if (c === '"') inString = true;
      else if (c === '{') {
        if (depth === 0) objStart = idx;
        depth++;
      } else if (c === '}') {
        depth--;
        if (depth === 0 && objStart !== -1) {
          const objText = text.slice(objStart, idx + 1);
          try {
            results.push(JSON.parse(objText));
          } catch (e) {}
          objStart = -1;
          if (results.length % 100 === 0) {
            self.postMessage({ type: 'progress', percent: 30 + Math.floor(idx / text.length * 60), text: `已提取 ${results.length} 组...` });
          }
        }
      } else if (c === ']' && depth === 0) {
        break;
      }
    }
  }
  return results;
}

self.onmessage = async (e) => {
  const { file } = e.data;
  try {
    self.postMessage({ type: 'progress', percent: 2, text: '流式 Worker: 分块读取文件...' });

    const text = await readFileInChunks(file, (read, total) => {
      const pct = 2 + Math.floor((read / total) * 30);
      self.postMessage({ type: 'progress', percent: pct, text: `流式读取中 ${(read / 1024 / 1024).toFixed(1)}MB / ${(total / 1024 / 1024).toFixed(1)}MB` });
    });

    self.postMessage({ type: 'progress', percent: 35, text: '流式 Worker: 提取 strong_candidates 数组元素...' });
    const strong = extractArrayObjects(text, 'strong_candidates');

    self.postMessage({ type: 'progress', percent: 80, text: strong.length ? `完成提取 ${strong.length} 组` : '未找到 strong_candidates，尝试整体解析...' });

    if (strong.length === 0) {
      const full = JSON.parse(text);
      self.postMessage({ type: 'progress', percent: 95, text: '整体解析完成' });
      self.postMessage({ type: 'result', data: full });
    } else {
      const fake = { strong_candidates: strong };
      self.postMessage({ type: 'progress', percent: 95, text: '流式完成' });
      self.postMessage({ type: 'result', data: fake });
    }
  } catch (err) {
    self.postMessage({ type: 'error', message: err.message });
  }
};