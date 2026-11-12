import numpy as np
from PIL import Image, ImageOps
import torch
import torch.nn.functional as F


def letterbox(path, size, patch_size, mean):
    with Image.open(path) as image:
        image=ImageOps.exif_transpose(image).convert('RGB')
        w,h=image.size
        scale=min(size/w,size/h)
        nw,nh=max(1,round(w*scale)),max(1,round(h*scale))
        x,y=(size-nw)//2,(size-nh)//2
        canvas=Image.new('RGB',(size,size),tuple(round(v*255) for v in mean))
        canvas.paste(image.resize((nw,nh),Image.Resampling.BILINEAR),(x,y))
    mask=torch.zeros(5,size,size)
    mask[0,y:y+nh,x:x+nw]=1
    # Quadrants use the valid image rectangle, not padded canvas coordinates.
    for i,(ya,yb,xa,xb) in enumerate([
        (y,y+nh//2,x,x+nw//2),(y,y+nh//2,x+nw//2,x+nw),
        (y+nh//2,y+nh,x,x+nw//2),(y+nh//2,y+nh,x+nw//2,x+nw)]):
        mask[i+1,ya:yb,xa:xb]=1
    weights=F.avg_pool2d(mask[:,None],patch_size,patch_size)[:,0]
    return canvas,weights,dict(original_size=[w,h],resized_size=[nw,nh],padding=[x,y,size-nw-x,size-nh-y])


def normalize(x):
    x=x.float()
    norms=x.norm(dim=-1,keepdim=True)
    if not torch.isfinite(x).all() or (norms<1e-12).any():
        raise ValueError('Nonfinite or zero embedding')
    return x/norms


def pool_features(tokens, weights, registers, grid, save_patches=False):
    if tokens.shape[0]!=1+registers+grid*grid:
        raise ValueError('Unexpected CLS/register/patch token count')
    patches=tokens[1+registers:].float().reshape(grid,grid,-1)
    weights=weights.to(patches.device)
    denominators=weights.sum((1,2))
    if (denominators<=0).any():raise ValueError('Empty valid spatial region')
    pooled=torch.einsum('qhw,hwd->qd',weights,patches)/denominators[:,None]
    result=dict(cls=normalize(tokens[0]),patch_mean=normalize(pooled[0]),
                spatial_2x2=normalize(pooled[1:].reshape(-1)))
    if save_patches:
        result.update(patches=normalize(patches),patch_valid_weights=weights[0])
    return {k:v.cpu().numpy().astype(np.float32) for k,v in result.items()}
