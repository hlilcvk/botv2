const fs = require('fs');
const path = require('path');

const srcPath = path.join(__dirname, '../tbx/proptrex_tbx.html');
const destJs = path.join(__dirname, 'engine/extracted_bot.js');

try {
  fs.mkdirSync(path.join(__dirname, 'engine'), { recursive: true });

  let content = fs.readFileSync(srcPath, 'utf-8');
  const scriptRegex = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  let largestScript = '';

  while ((match = scriptRegex.exec(content)) !== null) {
    if (match[1].length > largestScript.length) {
      largestScript = match[1];
    }
  }

  fs.writeFileSync(destJs, largestScript);
  console.log(`Extracted ${largestScript.length} bytes to engine/extracted_bot.js`);
} catch (e) {
  console.error(e);
}
