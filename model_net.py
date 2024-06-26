import torch
import torch.nn as nn
import torch.nn.functional as F
import random
from utils import DiffJPEG, USMSharp, filter2D
import numpy as np
from degradations import random_add_gaussian_noise_pt, random_add_poisson_noise_pt

class XXX_Net(nn.Module):
    def __init__(self):
        super(XXX_Net, self).__init__()
        self.jpeger = DiffJPEG(differentiable=False).cuda()  # simulate JPEG compression artifacts
        self.usm_sharpener = USMSharp().cuda()

    def forward(self, x):
        #---------前向传播代码---------#
        return x
    

    @torch.no_grad()
    def feed_data(self, data):
        """Accept data from dataloader, and then add two-order degradations to obtain LQ images.
        """
        # training data synthesis
        self.gt = data['gt'].to(self.device)
        # USM sharpen the GT images
        if self.opt["degradation"]['gt_usm'] is True:
            self.gt = self.usm_sharpener(self.gt)

        self.kernel1 = data['kernel1'].to(self.device)
        self.kernel2 = data['kernel2'].to(self.device)
        self.sinc_kernel = data['sinc_kernel'].to(self.device)

        ori_h, ori_w = self.gt.size()[2:4]

        # ----------------------- The first degradation process ----------------------- #
        # blur
        out = filter2D(self.gt, self.kernel1)
        # random resize
        updown_type = random.choices(['up', 'down', 'keep'], self.opt["degradation"]['resize_prob'])[0]
        if updown_type == 'up':
            scale = np.random.uniform(1, self.opt["degradation"]['resize_range'][1])
        elif updown_type == 'down':
            scale = np.random.uniform(self.opt["degradation"]['resize_range'][0], 1)
        else:
            scale = 1
        mode = random.choice(['area', 'bilinear', 'bicubic'])
        out = F.interpolate(out, scale_factor=scale, mode=mode)
        # add noise
        gray_noise_prob = self.opt["degradation"]['gray_noise_prob']
        if np.random.uniform() < self.opt["degradation"]['gaussian_noise_prob']:
            out = random_add_gaussian_noise_pt(
                out, sigma_range=self.opt["degradation"]['noise_range'], clip=True, rounds=False, gray_prob=gray_noise_prob)
        else:
            out = random_add_poisson_noise_pt(
                out,
                scale_range=self.opt["degradation"]['poisson_scale_range'],
                gray_prob=gray_noise_prob,
                clip=True,
                rounds=False)
        # JPEG compression
        if self.opt["degradation"]['jpeg_range'] is not None:
            jpeg_p = out.new_zeros(out.size(0)).uniform_(*self.opt["degradation"]['jpeg_range'])
            out = torch.clamp(out, 0, 1)  # clamp to [0, 1], otherwise JPEGer will result in unpleasant artifacts
            out = self.jpeger(out, quality=jpeg_p)

        # ----------------------- The second degradation process ----------------------- #
        # blur
        if np.random.uniform() < self.opt["degradation"]['second_blur_prob']:
            out = filter2D(out, self.kernel2)
        # random resize
        updown_type = random.choices(['up', 'down', 'keep'], self.opt["degradation"]['resize_prob2'])[0]
        if updown_type == 'up':
            scale = np.random.uniform(1, self.opt["degradation"]['resize_range2'][1])
        elif updown_type == 'down':
            scale = np.random.uniform(self.opt["degradation"]['resize_range2'][0], 1)
        else:
            scale = 1
        mode = random.choice(['area', 'bilinear', 'bicubic'])
        out = F.interpolate(
            out, size=(int(ori_h / self.opt['scale'] * scale), int(ori_w / self.opt['scale'] * scale)), mode=mode)
        # add noise
        gray_noise_prob = self.opt["degradation"]['gray_noise_prob2']
        if np.random.uniform() < self.opt["degradation"]['gaussian_noise_prob2']:
            out = random_add_gaussian_noise_pt(
                out, sigma_range=self.opt["degradation"]['noise_range2'], clip=True, rounds=False, gray_prob=gray_noise_prob)
        else:
            out = random_add_poisson_noise_pt(
                out,
                scale_range=self.opt["degradation"]['poisson_scale_range2'],
                gray_prob=gray_noise_prob,
                clip=True,
                rounds=False)

        # JPEG compression + the final sinc filter
        # We also need to resize images to desired sizes. We group [resize back + sinc filter] together
        # as one operation.
        # We consider two orders:
        #   1. [resize back + sinc filter] + JPEG compression
        #   2. JPEG compression + [resize back + sinc filter]
        # Empirically, we find other combinations (sinc + JPEG + Resize) will introduce twisted lines.
        if np.random.uniform() < 0.5:
            # resize back + the final sinc filter + JPEG compression
            mode = random.choice(['area', 'bilinear', 'bicubic'])
            out = F.interpolate(out, size=(ori_h // self.opt['scale'], ori_w // self.opt['scale']), mode=mode)
            out = filter2D(out, self.sinc_kernel)
            # JPEG compression
            if self.opt["degradation"]['jpeg_range2'] is not None:
                jpeg_p = out.new_zeros(out.size(0)).uniform_(*self.opt["degradation"]['jpeg_range2'])
                out = torch.clamp(out, 0, 1)
                out = self.jpeger(out, quality=jpeg_p)
        else:
            # JPEG compression
            if self.opt["degradation"]['jpeg_range2'] is not None:
                jpeg_p = out.new_zeros(out.size(0)).uniform_(*self.opt["degradation"]['jpeg_range2'])
                out = torch.clamp(out, 0, 1)
                out = self.jpeger(out, quality=jpeg_p)
            # resize back + the final sinc filter
            mode = random.choice(['area', 'bilinear', 'bicubic'])
            out = F.interpolate(out, size=(ori_h // self.opt['scale'], ori_w // self.opt['scale']), mode=mode)
            out = filter2D(out, self.sinc_kernel)

        # clamp and round
        lq = torch.clamp((out * 255.0).round(), 0, 255) / 255.
        return self.gt, lq
