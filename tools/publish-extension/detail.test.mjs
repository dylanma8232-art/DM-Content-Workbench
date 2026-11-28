import test from 'node:test';
import assert from 'node:assert/strict';
import {collectDetail} from './collect-detail.mjs';

test('note images keep slide order, omit carousel clones and mark missing pages', t => {
  const image = (index, cloned=false) => ({currentSrc:`https://images.example/${index}.png`,getClientRects:()=>[1],
    closest:selector=>selector.includes('duplicate') ? (cloned ? {} : null) : {getAttribute:()=>String(index)}});
  const text={innerText:'笔记正文',getClientRects:()=>[1],closest:()=>null};
  const root={getClientRects:()=>[1],matches:()=>false,querySelectorAll:selector=>{
    if (selector.startsWith('.note-slider')) return [image(2,true),image(2),image(1),image(1)];
    if (selector === '.swiper-pagination-bullet') return [1,2,3];
    if (selector.startsWith('#detail-desc')) return [text];
    return [];
  }};
  globalThis.document={querySelectorAll:()=>[root]};
  globalThis.location=new URL('https://www.xiaohongshu.com/explore/0123456789abcdef01234567');
  globalThis.getComputedStyle=()=>({visibility:'visible'});
  t.after(()=>{delete globalThis.document;delete globalThis.location;delete globalThis.getComputedStyle;});
  const result=collectDetail({platform:'xiaohongshu'},{external_id:'0123456789abcdef01234567',body:'简介'});
  assert.deepEqual(result.image_urls,['https://images.example/1.png','https://images.example/2.png']);
  assert.equal(result.media_kind,'image');
  assert.equal(result.images_incomplete,true);
});

test('detail reader uses selected work text and refuses a login redirect', t => {
  const item={external_id:'BV1xx411c7mD',title:'列表',body:'简介',url:'https://www.bilibili.com/video/BV1xx411c7mD'};
  const text={innerText:'作品详情中更完整的文字',getClientRects:()=>[1],closest:()=>null};
  const root={getClientRects:()=>[1],matches:()=>false,querySelectorAll:selector=>selector.startsWith('.video-desc') ? [text] : []};
  globalThis.document={querySelectorAll:()=>[root]};
  globalThis.getComputedStyle=()=>({visibility:'visible'});
  globalThis.location=new URL(item.url);
  t.after(()=>{delete globalThis.document;delete globalThis.getComputedStyle;delete globalThis.location;});
  const result=collectDetail({platform:'bilibili'},item);
  assert.equal(result.body,text.innerText);
  assert.equal(result.content_level,'detail_text');
  assert.equal(result.url,item.url);
  globalThis.location=new URL('https://passport.bilibili.com/login');
  const denied=collectDetail({platform:'bilibili'},item);
  assert.equal(denied.depth_status,'login_required');
  assert.equal(denied.body,'简介');
});
