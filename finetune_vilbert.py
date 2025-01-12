
import time
import numpy as np
import json
import torch
import torch.nn as nn

from sklearn.model_selection import train_test_split
from helper import  build_optimizer_for_allmodels 
from vilbert import MyVilBertFinetune,MyBertConfig

from torch.utils.data import DataLoader

from datasets import * 
import argparse

from transformers import (
    BertTokenizer,
    set_seed,
)
import os
device = "cuda"
import  random
def seed_everything(seed):
    set_seed(seed)
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    
def train(opt):
    seed_everything(opt.seed)
    
    tokenizer = BertTokenizer.from_pretrained(pretrained_model_name_or_path= opt.tokenizer_path)
  
    color_set = set()
    with open('./color.txt','r') as file:
        lines = file.readlines()
    for line in lines:
        color_set.add(line[:-1])
    
  
    with open(os.path.join('./data' ,'sort_label_list.txt'), 'r') as f:
        label_list = [label for label in f.read().strip().split()]
   
    label2id = {key:i for i, key in enumerate(label_list)}

   
    with open(os.path.join('./data','attr_to_attrvals.json'), 'r') as f:
        key_attr_values = json.loads(f.read())
   
    since = time.time()
    if opt.mode == 'train':
        coarse_train_path = os.path.join(opt.data_root, 'coarse_to_fine_data.json')
        fine_train_path = os.path.join(opt.data_root,'fine_data.json')
    else:
        coarse_train_path = os.path.join(opt.data_root,'fine_data_sample.json')
        fine_train_path = os.path.join(opt.data_root,'fine_data_sample.json')
    
    
    with open(fine_train_path, 'r') as f:
        fine_data = json.loads(f.read())
    fine_texts, fine_img_features, fine_labels, fine_label_masks, fine_key_attrs =\
        fine_data['texts'] , fine_data['img_features'] , fine_data['labels'] , fine_data['label_masks'] , fine_data['key_attrs']

    with open(coarse_train_path, 'r', encoding='utf-8') as f:
        coarse_to_fine_data = json.loads(f.read())
    coarse_to_fine_texts, coarse_to_fine_img_features, coarse_to_fine_labels, coarse_to_fine_label_masks, coarse_to_fine_key_attrs = \
        coarse_to_fine_data['texts'], coarse_to_fine_data['img_features'], coarse_to_fine_data['labels'], coarse_to_fine_data['label_masks'], coarse_to_fine_data['key_attrs']
        
    fine_texts = list(map(delete_word,fine_texts))
    coarse_to_fine_texts = list(map(delete_word,coarse_to_fine_texts))

    fine_data_texts = np.array(fine_texts)
    fine_data_img_features = np.array(fine_img_features)
    fine_data_labels = np.array(fine_labels)
    fine_data_label_masks = np.array(fine_label_masks)
    fine_data_key_attrs = np.array(fine_key_attrs)

    assert 0 < opt.test_rate < 1
    train_idxs, test_idxs = train_test_split(range(len(fine_data_texts)), test_size=opt.test_rate)

    train_fine_texts = fine_data_texts[train_idxs]
    train_fine_img_features = fine_data_img_features[train_idxs]
    train_fine_labels = fine_data_labels[train_idxs]
    train_fine_label_masks = fine_data_label_masks[train_idxs]
    train_fine_key_attrs = fine_data_key_attrs[train_idxs]

    train_texts         = np.concatenate((train_fine_texts, np.array(coarse_to_fine_texts)))
    train_img_features  = np.concatenate((train_fine_img_features, np.array(coarse_to_fine_img_features)))
    train_labels        = np.concatenate((train_fine_labels, np.array(coarse_to_fine_labels)))
    train_label_masks   = np.concatenate((train_fine_label_masks, np.array(coarse_to_fine_label_masks)))
    train_key_attrs     = np.concatenate((train_fine_key_attrs, np.array(coarse_to_fine_key_attrs)))
    
    test_texts = fine_data_texts[test_idxs]
    test_img_features = fine_data_img_features[test_idxs]
    test_labels = fine_data_labels[test_idxs]
    test_label_masks = fine_data_label_masks[test_idxs]
    test_key_attrs = fine_data_key_attrs[test_idxs]
    # comment my dataset
    train_dataset = MatchDataset_v2(
        tokenizer = tokenizer , 
        texts  = train_texts, 
        labels = train_labels, 
        visual_embeds = train_img_features,
        label_masks = train_label_masks,
        key_attrs  = train_key_attrs,
        key_attr_values = key_attr_values,
        label2id = label2id,
        color_set = color_set,
    )
    test_dataset = MatchDataset_v2(
        tokenizer = tokenizer , 
        texts = test_texts , 
        labels = test_labels , 
        visual_embeds = test_img_features , 
        label_masks = test_label_masks , 
        key_attrs = test_key_attrs , 
        key_attr_values = key_attr_values , 
        label2id = label2id,
        p6 = -1 ,          # 文本打乱
        p7 = -1,           # feats增强
        color_set = color_set,
    )
    
    train_dataloader = DataLoader(train_dataset, shuffle=True, batch_size=opt.batch_size, num_workers=opt.num_workers)
    test_dataloader = DataLoader(test_dataset, batch_size=opt.batch_size, num_workers=opt.num_workers)
    print('加载数据完成 %.2f min。 总共训练集 %d. 总测试集合 %d.'%((time.time()-since)/ 60,len(train_texts),len(test_texts)))
    config = MyBertConfig.from_json_file(os.path.join(opt.pretrain_model_path,'config.json'))
    
    pretrain_state_dict = torch.load(os.path.join(opt.pretrain_model_path,'pytorch_model.bin'))
    myvilbert = MyVilBertFinetune(config=config)
    keys = myvilbert.load_state_dict(pretrain_state_dict,strict=False)
    print('missing keys ',keys[0])
    print('unexpected_keys ',keys[1])
    myvilbert.to(device)

   
    pretrain_module = list(myvilbert.myvilbert.named_parameters())
    finetune_module = list(myvilbert.cls.named_parameters())
    print('加载模型 %s'%(opt.pretrain_model_path))

    criterion =  nn.BCELoss()
    total_update_step = opt.epochs * len(train_dataloader)
    
    optim , scheduler = build_optimizer_for_allmodels(opt  ,total_update_step , pretrain_module, finetune_module)
    record_epoch_arr = []
    best_score , min_loss = float('-inf') , float('inf')
    for epoch in range(opt.epochs):
        since = time.time() 
        myvilbert.train()
        for _ , batch in enumerate(train_dataloader):
            input_ids               = batch['input_ids'].to(device)                 # shape [batch_size,seq_len]
            attention_mask          = batch['attention_mask'].to(device)
            token_type_ids          = batch['token_type_ids'].to(device)            # shape [batch_size,seq_len]
            visual_embeds           = batch['visual_embeds'].to(device)             # shape [batch_size, feat_num, feat_dim]
            visual_attention_mask   = batch['visual_attention_mask'].to(device)     # shape [batch_size, feat_num]
            labels                  = batch['labels'].to(device)                    # shape [batch_size, 13]
            output = myvilbert(
                input_ids = input_ids,
                feats = visual_embeds,
                attention_mask = attention_mask,
                feats_attention_mask = visual_attention_mask,
                token_type_ids = token_type_ids,
            )   
            optim.zero_grad()
            imgtxt_loss = criterion(output[:,0],labels[:,0])
            attr_loss = criterion(output[:,1:],labels[:,1:])
            loss = imgtxt_loss + attr_loss
            loss.backward()
            optim.step()
            scheduler.step()

       
        N_img_text , M_img_text = 0, 0
        N_attr , M_attr = 0 , 0
        eval_losses , eval_img_text_losses , eval_attr_losses = [] , [] , []
        with torch.no_grad():
            myvilbert.eval()
            for batch in test_dataloader:
                input_ids               = batch['input_ids'].to(device)                 # shape [batch_size,seq_len]
                attention_mask          = batch['attention_mask'].to(device)
                token_type_ids          = batch['token_type_ids'].to(device)            # shape [batch_size,seq_len]
                visual_embeds           = batch['visual_embeds'].to(device)             # shape [batch_size, feat_num, feat_dim]
                visual_attention_mask   = batch['visual_attention_mask'].to(device)     # shape [batch_size, feat_num]
                labels                  = batch['labels'].to(device)                    # shape [batch_size, 13]    
                label_masks             = batch['label_masks'].to(device)
                output = myvilbert(
                    input_ids = input_ids,
                    feats = visual_embeds,
                    attention_mask = attention_mask,
                    feats_attention_mask = visual_attention_mask,
                    token_type_ids = token_type_ids,
                )   
                img_txt_logit , attr_logit = output[:,0] , output[:,1:]
                true_img_text , true_attr = labels[:,0] , labels[:,1:]
               
                img_text_loss = criterion(img_txt_logit ,true_img_text )
                attr_loss = criterion(attr_logit ,true_attr )
                test_loss = img_text_loss + attr_loss
                eval_losses.append(test_loss)
                eval_img_text_losses.append(img_text_loss)
                eval_attr_losses.append(attr_loss)

             
                pred_img_text = torch.zeros_like(output[:,0])
                pred_img_text[output[:,0] >= 0.5] =1
                N_img_text += torch.sum(pred_img_text == true_img_text).cpu().numpy().item()
                M_img_text += torch.sum(torch.ones_like(true_img_text)).cpu().numpy().item()
                
                pred_attr =  torch.zeros_like(output[:,1:])
                pred_attr[output[:,1:] >= 0.5] = 1
                pred_attr = pred_attr[label_masks[:, 1:] == 1]
                true_attr = true_attr[label_masks[:, 1:] == 1]
                N_attr += torch.sum(pred_attr == true_attr).cpu().numpy().item()
                M_attr += torch.sum(torch.ones_like(true_attr)).cpu().numpy().item()

                del input_ids, attention_mask , token_type_ids , visual_embeds , visual_attention_mask , labels , label_masks
                del output , img_txt_logit , attr_logit , true_img_text , true_attr , img_text_loss , attr_loss , test_loss

            eval_loss = (sum(eval_losses) / len(eval_losses)).detach().cpu().numpy().item()
            eval_img_text_loss = (sum(eval_img_text_losses) / len(eval_img_text_losses)).detach().cpu().numpy().item()
            eval_attr_loss = (sum(eval_attr_losses) / len(eval_attr_losses)).detach().cpu().numpy().item()
            img_text_scores = 0.5 * N_img_text / M_img_text
            attr_scores = 0.5 * N_attr / M_attr
            total_scores = img_text_scores + attr_scores
        is_save_model = 0
        if total_scores > best_score :  
            best_score , is_save_model = total_scores , 1
            save_model(myvilbert,tokenizer , opt ,model_type = 'score')
        if eval_loss < min_loss:   
            min_loss , is_save_model = eval_loss , 1 
            save_model(myvilbert , tokenizer , opt ,model_type = 'loss')
        using_time = (time.time()-since)/60
        print('E%d(t %.2f min) score %.4f (it %.4f attr %.4f) t_l %.6f (it %.6f attr %.6f)  SaveMode %d.'\
            %(epoch , using_time , total_scores , img_text_scores , attr_scores ,eval_loss , eval_img_text_loss ,eval_attr_loss   , is_save_model))

        record_epoch_arr.append([ total_scores , img_text_scores , attr_scores ,eval_loss , eval_img_text_loss ,eval_attr_loss])
    
    print('#'*25,' 训练完毕' , '#'*25)
    strings = time.strftime('%Y,%m,%d,%H,%M,%S')
    t = strings.split(',')
    number = [int(i) for i in t]
    dir_path = os.path.join(opt.output_root)
    if not os.path.exists(dir_path):
        os.mkdir(dir_path)
    full_path = os.path.join(dir_path,'static-%02d%02d.npy'%(number[3],number[4]))
    np.save(full_path,record_epoch_arr)
    print('#'*25,' 写入统计数据成功， 文件名' , full_path)
    write_opt(opt)
    del myvilbert , optim , scheduler
    torch.cuda.empty_cache()



def write_opt(opt):

    params = [attr for attr in dir(opt) if not attr.startswith('_')]
    content = ''
    for param in params:
        content += '%s : %s \n'%(param,opt.__getattribute__(param))
    strings = time.strftime('%Y,%m,%d,%H,%M,%S')
    t = strings.split(',')
    number = [int(i) for i in t]
    dir_path = os.path.join(opt.__getattribute__('output_root'))
    
    os.makedirs(dir_path , exist_ok=True)
    with open(os.path.join(dir_path,'opt_parm.txt'),'w+',encoding='utf-8') as file:
        file.write(content)
    
def save_model(model,tokenizer,opt,model_type):
    strings = time.strftime('%Y,%m,%d,%H,%M,%S')
    t = strings.split(',')
    number = [int(i) for i in t]
    dir_path = os.path.join(opt.output_root)  

    os.makedirs(dir_path,exist_ok=True)
    model_to_save = model.module if hasattr(model, 'module') else model
    torch.save(model_to_save.state_dict(), os.path.join(dir_path, 'pytorch_model.bin'))
    # model_to_save.config.to_json_file( os.path.join(dir_path, 'config.json'))
    config_str = model_to_save.config.to_json_string()
    with open(os.path.join(dir_path,'config.json'), 'w', encoding='utf-8') as f:
        f.write(config_str)
    tokenizer.save_vocabulary(dir_path)

def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed',type = int, default=25, help='random seed')
    parser.add_argument('--mode',type=str,default='train',help='train:正式训练; test:代码测试阶段')
    parser.add_argument('--tokenizer_path',type=str,default = './vilbert_model/pretrain/' ,help='tokenizer path') 
    parser.add_argument('--pretrain_model_path',type=str,default = './vilbert_model/pretrain/' ,help='pretrain model path') 
    parser.add_argument('--data_root',type = str , default='./data/',help='数据根路径')
    parser.add_argument('--output_root',type = str , default='./vilbert_model/finetune/',help = '输出根路径' )
    parser.add_argument('--num_workers',type =int,default=16)
    parser.add_argument('--test_rate',type = float,default=0.2 , help='测试集的比例')
    parser.add_argument('--epochs',type=int,default=50)
    parser.add_argument('--lr',type=float,default=8e-5) #TODO 还在搜索，感觉可以更大
    parser.add_argument('--small_lr',type=float,default=2e-5)   #TODO 还在搜索，感觉可以更大
    parser.add_argument('--batch_size',type = int,default=640)
    parser.add_argument('--weight_decay', type=float, default=1e-4, help='weight_decay')   
    parser.add_argument('--gpu',type = int, default=1,help='GPU')
    parser.add_argument('--warmup_ratio', type=float, default=0.1, help='warmup_ratio')
    opt = parser.parse_args()
    return opt    

if __name__ == '__main__':
    opt = parse_opt()
    print(opt)  
    os.environ['CUDA_VISIBLE_DEVICES'] = str(opt.gpu)
    train(opt)



