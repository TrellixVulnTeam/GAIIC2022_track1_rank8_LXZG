
import torch
import torch.nn as nn
import numpy as np
from transformers.models.lxmert.modeling_lxmert import (
    LxmertPreTrainedModel,
    LxmertPooler ,
    LxmertLayer  ,
    LxmertXLayer ,
    LxmertModelOutput,
    LxmertPreTrainingHeads,
    LxmertConfig,
    LxmertEmbeddings,
)
from transformers import BertTokenizer
device = 'cuda'

class MyVisualFeatureEncoder(nn.Module):
    def __init__(self,config):
        super().__init__()
        feat_dim = config.visual_feat_dim
        self.visn_fc = nn.Linear(feat_dim,config.hidden_size)
        self.visn_layer_norm = nn.LayerNorm(config.hidden_size , eps = 1e-12)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        
    def forward(
        self,
        visual_feats
    ):
        return self.dropout(self.visn_layer_norm(self.visn_fc(visual_feats)))
        
class MyLxmertEncoder(nn.Module):
    def __init__(self,config):
        super(MyLxmertEncoder,self).__init__()
        self.config = config
        self.num_l_layers = config.l_layers    
        self.num_x_layers = config.x_layers     
        self.num_r_layers = config.r_layers     

        self.txt_embedding = LxmertEmbeddings(config)
        self.l_layers  = nn.ModuleList([LxmertLayer(config) for _ in range(self.num_l_layers)])
        self.x_layers   = nn.ModuleList([LxmertXLayer(config) for _ in range(self.num_x_layers)])
        self.r_layers   = nn.ModuleList([LxmertLayer(config) for _ in range(self.num_r_layers)])
    def forward(
        self ,
        input_ids , 
        token_type_ids , 
        attention_mask, 
        extended_attention_mask , # extended
        
        visual_feats , 
        visual_attention_mask ,
    ): 

       
        txt_embeddings = self.txt_embedding(input_ids,token_type_ids)
        
        lang_feats = txt_embeddings
        for layer_module in self.l_layers:
            l_output = layer_module(lang_feats,extended_attention_mask)
            lang_feats = l_output[0]
        for layer_module in self.r_layers:
            v_outputs = layer_module(visual_feats, visual_attention_mask)
            visual_feats = v_outputs[0]
        for layer_module in self.x_layers:
            x_outputs = layer_module(
                lang_feats,
                extended_attention_mask,
                visual_feats,
                visual_attention_mask,
            )
            lang_feats, visual_feats = x_outputs[:2]
        return visual_feats , lang_feats  




class MyLxmert(LxmertPreTrainedModel):
    def __init__(self,config):
        super().__init__(config)
        self.visn_fc = MyVisualFeatureEncoder(config)



        self.encoder = MyLxmertEncoder(config)
        self.pooler = LxmertPooler(config)
        self.post_init()
    def forward(
        self,
        input_ids , 
        attention_mask , 
        token_type_ids , 

        visual_feats ,  
        visual_attention_mask ,
    ):
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = extended_attention_mask.to(dtype=self.dtype)
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0
        extended_visual_attention_mask = visual_attention_mask.unsqueeze(1).unsqueeze(2)
        extended_visual_attention_mask = extended_visual_attention_mask.to(dtype=self.dtype)
        extended_visual_attention_mask = (1.0 - extended_visual_attention_mask) * -10000.0
        visual_feats = self.visn_fc(visual_feats)
        visual_feats , lang_feats   = self.encoder(
            input_ids = input_ids, 
            token_type_ids = token_type_ids, 
            attention_mask = attention_mask, 
            extended_attention_mask = extended_attention_mask,
            visual_feats = visual_feats , 
            visual_attention_mask  = extended_visual_attention_mask,
        )
        
        pooled_output = self.pooler(lang_feats)
        
        return LxmertModelOutput(
            pooled_output= pooled_output,
            language_output=lang_feats, 
            vision_output=visual_feats, 
        )


class MyLxmertForPreTraining(LxmertPreTrainedModel):
    def __init__(self,config):
        super().__init__(config)
        self.config = config  
        self.mylxmert = MyLxmert(config)
        self.pretrain_task = LxmertPreTrainingHeads(config, self.mylxmert.encoder.txt_embedding.word_embeddings.weight)

        self.post_init()
        self.ce_loss = nn.CrossEntropyLoss()
         
    def forward(
        self,
        input_ids , 
        attention_mask ,
        token_type_ids , 
        visual_feats ,
        visual_attention_mask , 
        is_paired , 
        
        mlm_true_label,  
    ):
        
        output = self.mylxmert(
            input_ids=input_ids,
            visual_feats=visual_feats,
            token_type_ids=token_type_ids,
            attention_mask=attention_mask,
            visual_attention_mask=visual_attention_mask,
        )
        
        pooled_output = output.pooled_output
        lang_output = output.language_output        
        lang_prediction_scores, cross_relationship_score = self.pretrain_task(lang_output, pooled_output)
        
        matched_loss = self.ce_loss(cross_relationship_score.view(-1, 2), is_paired.view(-1))
        
        pred_match_txt = lang_prediction_scores[is_paired.view(-1)==1]  
        true_match_text = mlm_true_label[is_paired.view(-1)==1]                 

        if true_match_text.shape[0] > 0:
            masked_lm_loss = self.ce_loss(pred_match_txt.view(-1, self.config.vocab_size), true_match_text.view(-1))
        else:
            # 如果全0
            masked_lm_loss = torch.tensor(0.0).to(device=device)

        _ , idx = cross_relationship_score.max(1)
        right_match = (is_paired.squeeze(1) == idx).sum().item()
        
        return {
            'right_match' : right_match    ,      
            'mlm_loss'    : masked_lm_loss ,  
            'match_loss'  : matched_loss   ,   
        }


class MyLxmertFinetune(LxmertPreTrainedModel):
    def __init__(self,config,output_dim = 13):
        super().__init__(config)
        self.config = config
        self.output_dim = output_dim
        self.mylxmert = MyLxmert(config)       
        self.cls = nn.Linear(config.hidden_size, output_dim)
        self.sigmoid = nn.Sigmoid()
    def forward(
        self,
        input_ids , 
        attention_mask , 
        token_type_ids , 
        visual_feats ,  
        visual_attention_mask ,
    ):
        output = self.mylxmert(
            input_ids=input_ids,
            visual_feats=visual_feats,
            token_type_ids=token_type_ids,
            attention_mask=attention_mask,
            visual_attention_mask=visual_attention_mask,
        )
        pooled_output = output.pooled_output
        output = self.sigmoid(self.cls(pooled_output))
        return output



        
if __name__ == '__main__':

    batch_size = 32
    seq_len = 17
    input_ids = torch.tensor(np.random.randint(10,100,size=(batch_size ,seq_len)))
    token_type_ids = torch.tensor(np.random.randint(0,1,size=(batch_size ,seq_len)))
    attention_mask = torch.tensor(np.random.randint(0,1,size=(batch_size ,seq_len)))

    visual_feats = torch.rand(batch_size , 1, 2048)
    visual_attention_mask = torch.rand(batch_size , 1)

    mask_pos = torch.tensor(np.random.randint(0,10,size=(batch_size ,5)))

    print('input_ids : ',input_ids.size())
    print('token_type_ids : ',token_type_ids.size())
    print('attention_mask : ',attention_mask.size())
    print('visual_feats : ',visual_feats.size())
    print('visual_attention_mask : ',visual_attention_mask.size())
    print('mask_pos : ',mask_pos.size())


    tokenizer = BertTokenizer.from_pretrained(pretrained_model_name_or_path= './models/chinese_roberta_wwm_ext_pytorch')
    config = LxmertConfig(vocab_size = tokenizer.vocab_size)
    model = MyLxmert(config)

    _ , match_output = model(
        input_ids =  input_ids, 
        attention_mask =attention_mask, 
        token_type_ids  =token_type_ids, 

        visual_feats =visual_feats,  
        visual_attention_mask =visual_attention_mask,
    )
    # lang_output = model_output.language_output
    # pooled_output = model_output.pooled_output
    # visn_output = model_output.vision_output


    
    
    
    
    # print(lang_output.size())
    # print(visn_output.size())


