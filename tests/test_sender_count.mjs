import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),{pageProfileCount}=require('../userscripts/douyin-send.user.js');
const author='https://www.douyin.com/user/MS4wLjABAAAA_test_author';
const userInfo={secUid:'MS4wLjABAAAA_test_author',awemeCount:70};
const tab={__reactFiber$test:{memoizedProps:{},return:{memoizedProps:{userInfo,currentTab:'post',setCurrentTab(){}}}}};
const doc={querySelectorAll:()=>[tab]};
assert.equal(typeof pageProfileCount,'function');
assert.equal(pageProfileCount(doc,author,author),70,'当前主页直接复用已渲染的作者资料');
assert.equal(pageProfileCount(doc,author+'?modal_id=7421234567890123456',author),70,'本人主页打开作品时仍可复用本人的主页资料');
assert.equal(pageProfileCount(doc,'https://www.douyin.com/?recommend=1',author),null,'推荐页不能误用背景主页');
assert.equal(pageProfileCount(doc,author,author.replace('test_author','other_author')),null,'SPA切换地址后不能误读旧主页');
for(const invalid of [-1,1.5,'1.2万',null]){userInfo.awemeCount=invalid;assert.equal(pageProfileCount(doc,author,author),null);}
userInfo.awemeCount=0;assert.equal(pageProfileCount(doc,author,author),0);
tab.__reactFiber$test.return=tab.__reactFiber$test;assert.equal(pageProfileCount(doc,author,author),null,'异常资料链不死循环');
assert.equal(pageProfileCount({querySelectorAll:()=>[]},author,author),null);
console.log('PASS: 当前主页资料、作者归属、主页作品总数、零值及无效数据');
