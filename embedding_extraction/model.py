import torch
from transformers import AutoImageProcessor, AutoModel
from .preprocess import pool_features


class Extractor:
    def __init__(self,cfg):
        device=cfg['device']
        if device.startswith('cuda') and not torch.cuda.is_available():device='cpu'
        dtype=cfg['dtype']
        if device=='cpu' or (dtype=='bf16' and not torch.cuda.is_bf16_supported()):dtype='fp32'
        self.device=device; self.dtype=dtype; self.cfg=cfg
        self.processor=AutoImageProcessor.from_pretrained(cfg['model_path'],local_files_only=True)
        self.model=AutoModel.from_pretrained(cfg['model_path'],local_files_only=True,
            dtype={'bf16':torch.bfloat16,'fp16':torch.float16,'fp32':torch.float32}[dtype]).to(device).eval()
        c=self.model.config
        if c.model_type!='dinov3_vit':raise ValueError('Only DINOv3 ViT token layout is supported')
        self.patch=c.patch_size; self.dim=c.hidden_size; self.registers=c.num_register_tokens
        self.depth=c.num_hidden_layers; self.k=cfg['layer_from_end']
        if not 1<=self.k<=self.depth:raise ValueError('layer_from_end out of range')
        if cfg['image_size']%self.patch:raise ValueError('image_size must divide patch size')
        self.captured=None
        self.hook=None
        if self.k>1:
            self.hook=self.model.model.layer[-self.k].register_forward_hook(self.capture)

    def capture(self,module,args,output):
        self.captured=output[0] if isinstance(output,tuple) else output

    @torch.inference_mode()
    def extract(self,images,weights):
        self.captured=None
        batch=self.processor(images=images,do_resize=False,return_tensors='pt')
        pixels=batch['pixel_values']
        if tuple(pixels.shape[-2:])!=(self.cfg['image_size'],)*2:
            raise ValueError('Processor unexpectedly changed image dimensions')
        output=self.model(pixel_values=pixels.to(self.device),output_hidden_states=False)
        if self.k==1:tokens=output.last_hidden_state
        else:
            if self.captured is None:raise RuntimeError('Selected block hook did not fire')
            tokens=self.model.norm(self.captured)
        if tokens.shape[-1]!=self.dim:raise ValueError('Hidden dimension mismatch')
        values=[pool_features(t,w,self.registers,self.cfg['image_size']//self.patch,
                              self.cfg['save_patches']) for t,w in zip(tokens,weights)]
        self.captured=None
        return values
